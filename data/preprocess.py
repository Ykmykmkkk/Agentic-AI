# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import pandas as pd
import streamlit as st
from bs4 import BeautifulSoup

_KRW_RE = re.compile(r'(krw|원|won)', re.IGNORECASE)

def _localname(qname: str) -> str:
    if not qname:
        return ''
    qname = qname.split('}')[-1]
    return qname.split(':')[-1].lower()

def _is_consolidated_context(ctx) -> bool | None:
    seg = ctx.find(lambda t: t.name and t.name.lower().endswith('segment'))
    if not seg:
        return None
    text = ' '.join([el.get_text(' ') for el in seg.find_all()])
    t = text.lower()
    if any(k in t for k in ['consolidated', '연결', 'cfs', 'consolidatedmember', 'consolidatedgroupmember', 'dart:cfs']):
        return True
    if any(k in t for k in ['separate', '별도', 'separatemember', 'dart:separate']):
        return False
    return None


class FinancialDataProcessor:
    """수동 업로드 XBRL → 연결 손익계산서의 분기(QTD) 값으로 정리"""

    # 정확 매핑(로컬네임 위주)
    CONCEPT_MAP = {
        '매출액': [
            'ifrs-full:Revenue','revenue','sales','salesrevenue','operatingrevenue',
            'revenuefromcontractswithcustomers'
        ],
        '매출원가': ['costofsales','costofrevenue','costofgoodssold'],
        '매출총이익': ['grossprofit','grossincome'],
        '판매비와관리비': ['sellinggeneraladministrativeexpenses','sellingandadministrativeexpenses','sga'],
        '영업이익': ['profitlossfromoperatingactivities','operatingincome','operatingprofit'],
        '당기순이익': ['profitloss','netincome','netprofit'],
        # 보조 항목
        '영업외수익': ['nonoperatingincome','otherincome','financialincome'],
        '영업외비용': ['nonoperatingexpense','otherexpense','financialcost','interestexpense']
    }

    # 백업용 정규식
    CONCEPT_REGEX = {
        '매출액': r'(revenue|sales)$',
        '매출원가': r'(costofsales|costofrevenue|costofgoods)$',
        '매출총이익': r'(grossprofit)$',
        '판매비와관리비': r'(selling.*administrative|sga$)',
        '영업이익': r'(profitlossfromoperatingactivities|operatingprofit|operatingincome)$',
        '당기순이익': r'(profitloss$|netincome$|netprofit$)',
    }

    # DART API(fnlttSinglAcntAll) account_nm → 표준 항목명 매핑
    DART_ACCOUNT_MAP = {
        '매출액': ['매출액', '수익(매출액)', '영업수익'],
        '매출원가': ['매출원가'],
        '매출총이익': ['매출총이익'],
        '판매비와관리비': ['판매비와관리비'],
        '영업이익': ['영업이익', '영업이익(손실)'],
        '당기순이익': ['당기순이익', '당기순이익(손실)', '반기순이익', '분기순이익'],
    }

    def __init__(self, debug: bool=False):
        self.debug = debug

    # ---------------- I/O ----------------

    def load_file(self, uploaded_file):
        try:
            size = uploaded_file.size if hasattr(uploaded_file, 'size') else 0
            if size > 50*1024*1024:
                st.error("❌ 50MB 이하 파일만 지원합니다.")
                return None
            uploaded_file.seek(0)
            content = uploaded_file.read()
            xml = self._fast_decode(content)
            if not xml:
                st.error("❌ 파일 인코딩을 읽을 수 없습니다.")
                return None

            soup = self._safe_parse(xml)
            company = self._extract_company_name(soup, uploaded_file.name)

            facts = self._xbrl_to_facts(soup, company)
            if facts.empty:
                st.error("❌ XBRL fact를 읽지 못했습니다.")
                return None

            # report type: 최신 연도의 최대 종료월로 판정
            rpt = self._guess_report_type_by_month(facts)
            if self.debug:
                with st.expander("🧭 XBRL Context 분류 디버그"):
                    latest_year = self._latest_duration_year(facts)
                    st.write(f"ReportType: {rpt}, LatestYear: {latest_year}")
                    sample = facts[['context_id','period_type','start','end']].drop_duplicates().head(20)
                    st.dataframe(sample, use_container_width=True)

            # 연결+KRW+대상 분기 윈도우로 슬라이스 (최신 연도 우선)
            sliced = self._slice_to_quarter(facts, rpt)
            if sliced.empty:
                st.warning("⚠️ QTD 컨텍스트를 못 찾아서 YTD 보정/백업 스캐너로 시도합니다.")
                sliced = self._slice_to_quarter_fallback(facts, rpt)

            items = self._facts_to_items(sliced)
            if not items:
                st.info("ℹ️ facts 매핑 실패 → 문서 패턴 스캐너 시도")
                items = self._backup_scan(soup)

            if not items:
                st.error("❌ 손익 항목을 찾지 못했습니다.")
                return None

            df = self._build_statement(items, company)
            return df

        except Exception as e:
            st.error(f"❌ 파일 처리 중 오류: {e}")
            return None

    # ---------------- XML helpers ----------------

    def _safe_parse(self, content_str: str) -> BeautifulSoup:
        try:
            soup = BeautifulSoup(content_str, 'lxml-xml')
            if not soup.find():
                soup = BeautifulSoup(content_str, 'xml')
        except Exception:
            soup = BeautifulSoup(content_str, 'html.parser')
        return soup

    def _fast_decode(self, content: bytes) -> str | None:
        for enc in ['utf-8','utf-8-sig','cp949','euc-kr','iso-8859-1','ascii']:
            try:
                return content.decode(enc)
            except Exception:
                continue
        try:
            return content.decode('utf-8', errors='ignore')
        except Exception:
            return None

    def _extract_company_name(self, soup, filename):
        for t in ['EntityRegistrantName','CompanyName','ReportingEntityName','EntityName','CorporateName']:
            n = soup.find(t) or soup.find(lambda x: x.name and t.lower() in x.name.lower())
            if n and n.string and n.string.strip():
                return n.string.strip()
        name = (filename or '').split('.')[0].lower()
        mapping = {
            'sk':'SK에너지','skenergy':'SK에너지',
            'gs':'GS칼텍스','gscaltex':'GS칼텍스',
            'hd':'HD현대오일뱅크','hyundai':'HD현대오일뱅크','hdoil':'HD현대오일뱅크',
            's-oil':'S-Oil','soil':'S-Oil','soilcorp':'S-Oil'
        }
        for k,v in mapping.items():
            if k in name: return v
        clean = re.sub(r'[^A-Za-z가-힣0-9\s]','', name) or "Unknown Company"
        return clean

    # ---------------- Facts building ----------------

    def _xbrl_to_facts(self, soup: BeautifulSoup, company: str) -> pd.DataFrame:
        # contexts
        ctx_rows = []
        for c in soup.find_all(lambda t: t.name and t.name.lower().endswith('context')):
            cid = c.get('id')
            if not cid: continue
            period = c.find(lambda t: t.name and t.name.lower().endswith('period'))
            start = end = inst = None
            ptype = 'instant'
            if period:
                s = period.find(lambda t: t.name and t.name.lower().endswith('startdate'))
                e = period.find(lambda t: t.name and t.name.lower().endswith('enddate'))
                i = period.find(lambda t: t.name and t.name.lower().endswith('instant'))
                if s and s.text: start = s.text.strip()
                if e and e.text: end   = e.text.strip()
                if i and i.text: inst  = i.text.strip()
                if start and end: ptype = 'duration'
            ctx_rows.append({
                'context_id': cid,
                'period_type': ptype,
                'start': start,
                'end': end or inst,
                'is_consolidated': _is_consolidated_context(c),
            })
        ctx_df = pd.DataFrame(ctx_rows)
        if ctx_df.empty:
            return pd.DataFrame()

        ctx_df['start'] = pd.to_datetime(ctx_df['start'], errors='coerce')
        ctx_df['end']   = pd.to_datetime(ctx_df['end'], errors='coerce')

        # units
        units = {}
        for u in soup.find_all(lambda t: t.name and t.name.lower().endswith('unit')):
            uid = u.get('id')
            if not uid: continue
            m = u.find(lambda t: t.name and t.name.lower().endswith('measure'))
            units[uid] = (m.text.strip() if m and m.text else None)

        # facts
        rows = []
        facts = soup.find_all(lambda t: (t.name and (t.get('contextRef') or t.get('contextref'))))
        for el in facts:
            text = (el.text or '').strip()
            if not text: continue
            try:
                val = float(re.sub(r'[^\d\.-]', '', text.replace('(', '-').replace(')', '')))
            except Exception:
                continue
            cref = el.get('contextRef') or el.get('contextref')
            uref = el.get('unitRef')    or el.get('unitref')
            rows.append({
                '회사': company,
                'concept_qname': el.name,
                'concept_local': _localname(el.name),
                'value': val,
                'unit': units.get(uref, uref),
                'context_id': cref,
            })
        df = pd.DataFrame(rows)
        if df.empty:
            return df

        df = df.merge(ctx_df, on='context_id', how='left')
        return df

    # ---- 최신 연도/분기 판정 ----
    def _latest_duration_year(self, facts: pd.DataFrame) -> int:
        dur = facts[facts['period_type'] == 'duration'].copy()
        ends = dur['end'].dropna()
        if ends.empty:
            # 안전장치: 전부 instant면 현재 연도 사용
            return pd.Timestamp.today().year
        return int(ends.max().year)

    def _guess_report_type_by_month(self, facts: pd.DataFrame) -> str:
        dur = facts[facts['period_type']=='duration'].copy()
        if dur['end'].notna().any():
            latest_end = dur['end'].max()
            return {3:'Q1',6:'Q2',9:'Q3',12:'Q4'}.get(int(latest_end.month),'Q3')
        return 'Q3'

    # ------------- Quarter slicing -------------
    def _slice_to_quarter(self, facts: pd.DataFrame, report_type: str) -> pd.DataFrame:
        # 1) 최신 연도 먼저 결정 (필터 적용 전에)
        latest_year = self._latest_duration_year(facts)

        # 2) KRW & 연결 우선 필터
        f = facts.copy()
        if 'unit' in f.columns:
            f = f[f['unit'].astype(str).str.contains(_KRW_RE, na=False)]
        if 'is_consolidated' in f.columns and f['is_consolidated'].notna().any():
            cfs = f['is_consolidated'] == True
            if cfs.any(): f = f[cfs]

        # 3) 최신 연도만 남기기 (필터 후 비었다면, 필터 없이 연도만 제한)
        f_year = f[(f['period_type']=='duration') & (f['end'].dt.year == latest_year)].copy()
        if f_year.empty:
            f_year = facts[(facts['period_type']=='duration') & (facts['end'].dt.year == latest_year)].copy()

        if f_year.empty:
            return pd.DataFrame()

        def pick(frame, start_m, end_m):
            m = (frame['start'].dt.month==start_m) & (frame['end'].dt.month==end_m)
            return frame[m]

        # QTD 우선순위
        if report_type=='Q1':
            qtd = pick(f_year, 1,3)
            ytd = pick(f_year, 1,3)  # Q1은 YTD==QTD
            prev = pd.DataFrame()
        elif report_type=='Q2':
            qtd = pick(f_year, 4,6)
            ytd = pick(f_year, 1,6)
            prev = pick(f_year, 1,3)
            if qtd.empty and not ytd.empty and not prev.empty:
                qtd = self._diff(ytd, prev)
        elif report_type=='Q3':
            qtd = pick(f_year, 7,9)
            ytd = pick(f_year, 1,9)
            prev = pick(f_year, 1,6)
            if qtd.empty and not ytd.empty and not prev.empty:
                qtd = self._diff(ytd, prev)
        else:  # Q4
            qtd = pick(f_year, 10,12)
            ytd = pick(f_year, 1,12)
            prev = pick(f_year, 1,9)
            if qtd.empty and not ytd.empty and not prev.empty:
                qtd = self._diff(ytd, prev)

        if self.debug:
            with st.expander("🔎 최신연도 슬라이스 디버그"):
                st.write(f"LatestYear={latest_year}, ReportType={report_type}")
                st.write(f"Rows(QTD)={len(qtd)}, Rows(YTD)={len(ytd) if 'ytd' in locals() else 0}")

        return qtd

    def _slice_to_quarter_fallback(self, facts: pd.DataFrame, report_type: str) -> pd.DataFrame:
        """컨텍스트 id 문자열 패턴 + 최신연도 제한 보조 선택"""
        latest_year = self._latest_duration_year(facts)
        dur = facts[(facts['period_type']=='duration') & (facts['end'].dt.year==latest_year)].copy()
        if dur.empty:
            return dur
        if report_type=='Q3':
            pat = re.compile(r'(7.?01|07.?01).*(9.?30|09.?30)')
        elif report_type=='Q2':
            pat = re.compile(r'(4.?01|04.?01).*(6.?30|06.?30)')
        elif report_type=='Q1':
            pat = re.compile(r'(1.?01|01.?01).*(3.?31|03.?31)')
        else:
            pat = re.compile(r'(10.?01).*(12.?31)')
        mask = dur['context_id'].astype(str).str.contains(pat, regex=True, na=False)
        return dur[mask]

    def _diff(self, ytd: pd.DataFrame, prev: pd.DataFrame) -> pd.DataFrame:
        """동일 concept/local 기준으로 ytd - prev 차감"""
        y = ytd[['concept_local','concept_qname','value','unit','context_id','start','end']].copy()
        p = prev[['concept_local','value']].rename(columns={'value':'prev'}).copy()
        out = y.merge(p, on='concept_local', how='left')
        out['value'] = out['value'] - out['prev'].fillna(0)
        out = out.drop(columns=['prev'])
        out['context_id'] = out['context_id'].astype(str) + '_QTD'
        return out

    # ------------- Mapping to items -------------
    def _facts_to_items(self, df: pd.DataFrame) -> dict:
        if df is None or df.empty:
            return {}
        items = {}

        # 1) 정확 매핑(여러 후보 중 절댓값 큰 값)
        for std, cands in self.CONCEPT_MAP.items():
            vals = []
            for q in cands:
                key = _localname(q)
                vals.append(df[df['concept_local']==key]['value'])
            s = pd.concat(vals) if vals else pd.Series(dtype=float)
            if not s.empty:
                v = s.reindex(s.abs().sort_values(ascending=False).index).iloc[0]
                items[std] = float(v)

        # 2) 정규식 보강
        for std, rx in self.CONCEPT_REGEX.items():
            if std in items: continue
            m = df['concept_local'].str.contains(rx, regex=True, na=False)
            if m.any():
                s = df.loc[m, 'value']
                v = s.reindex(s.abs().sort_values(ascending=False).index).iloc[0]
                items[std] = float(v)

        # 파생
        if '매출총이익' not in items and '매출액' in items and '매출원가' in items:
            items['매출총이익'] = items['매출액'] - items['매출원가']
        if '영업이익' not in items and '매출총이익' in items and '판매비와관리비' in items:
            items['영업이익'] = items['매출총이익'] - items['판매비와관리비']

        return items

    # ------------- Backup scanner -------------
    def _backup_scan(self, soup: BeautifulSoup) -> dict:
        items, processed = {}, 0
        numeric = [t for t in soup.find_all() if t.string and re.search(r'\d', t.string)]
        for tag in numeric:
            txt = tag.string.strip()
            try:
                num = float(re.sub(r'[^\d\.-]', '', txt.replace('(', '-').replace(')', '')))
            except Exception:
                continue
            if abs(num) < 10000:
                continue
            parts = [tag.name.lower() if tag.name else '']
            if tag.attrs:
                parts.extend([str(v).lower() for v in tag.attrs.values()])
            if tag.parent and tag.parent.name:
                parts.append(tag.parent.name.lower())
            info = ' '.join(parts)
            for rx, std in {
                r'(revenue|sales)(?!.*cost)': '매출액',
                r'(cost.*(sales|goods))|매출원가|원가': '매출원가',
                r'(gross.*profit|매출총이익)': '매출총이익',
                r'(selling.*administrative|판관비|판매비.*관리비)': '판매비와관리비',
                r'(operating.*(income|profit)|영업이익)': '영업이익',
                r'(profitloss$|netincome$|netprofit$|당기순이익)': '당기순이익'
            }.items():
                if re.search(rx, info, re.IGNORECASE):
                    if std not in items or abs(num) > abs(items[std]):
                        items[std] = num
        return items

    # ------------- Statement / ratios / merge -------------
    def _build_statement(self, data: dict, company: str) -> pd.DataFrame:
        order = ['매출액','매출원가','매출총이익','판매비와관리비','영업이익','영업외수익','영업외비용','당기순이익']
        rows = []
        for k in order:
            if k in data:
                rows.append({'구분': k, company: self._fmt_amt(data[k]), f'{company}_원시값': data[k]})
        sales = data.get('매출액', 0)
        if sales:
            def r(name, num): rows.append({'구분': name, company: f"{(num/sales)*100:.2f}%", f'{company}_원시값': (num/sales)*100})
            if '영업이익' in data: r('영업이익률(%)', data['영업이익'])
            if '매출총이익' in data: r('매출총이익률(%)', data['매출총이익'])
            if '당기순이익' in data: r('순이익률(%)', data['당기순이익'])
            if '매출원가' in data: r('매출원가율(%)', data['매출원가'])
            if '판매비와관리비' in data: r('판관비율(%)', data['판매비와관리비'])
        return pd.DataFrame(rows)

    def _fmt_amt(self, v: float) -> str:
        if v == 0: return "0원"
        sign = "▼ " if v < 0 else ""
        a = abs(v)
        if a >= 1_000_000_000_000: return f"{sign}{v/1_000_000_000_000:.1f}조원"
        if a >= 100_000_000:        return f"{sign}{v/100_000_000:.0f}억원"
        if a >= 10_000:             return f"{sign}{v/10_000:.0f}만원"
        return f"{sign}{v:,.0f}원"

    def process_dart_data(self, df: pd.DataFrame, company_name: str) -> pd.DataFrame | None:
        """DART API(fnlttSinglAcntAll)가 반환한 원본 재무제표 → 표준 손익 요약 DataFrame"""
        if df is None or df.empty:
            return None
        try:
            is_df = df[df['sj_div'].isin(['IS', 'CIS'])].copy() if 'sj_div' in df.columns else df.copy()
            if is_df.empty:
                is_df = df.copy()

            def parse_amount(v):
                try:
                    return float(str(v).replace(',', ''))
                except Exception:
                    return None

            items = {}
            for std, candidates in self.DART_ACCOUNT_MAP.items():
                match = is_df[is_df['account_nm'].astype(str).str.strip().isin(candidates)]
                if not match.empty:
                    val = parse_amount(match.iloc[0].get('thstrm_amount'))
                    if val is not None:
                        items[std] = val

            if '매출총이익' not in items and '매출액' in items and '매출원가' in items:
                items['매출총이익'] = items['매출액'] - items['매출원가']
            if '영업이익' not in items and '매출총이익' in items and '판매비와관리비' in items:
                items['영업이익'] = items['매출총이익'] - items['판매비와관리비']

            if not items:
                st.warning(f"⚠️ {company_name}: 손익 항목을 찾지 못했습니다.")
                return None

            return self._build_statement(items, company_name)
        except Exception as e:
            st.warning(f"⚠️ {company_name} 데이터 처리 중 오류: {e}")
            return None

    def merge_company_data(self, dataframes: list[pd.DataFrame]):
        if not dataframes: return pd.DataFrame()
        if len(dataframes) == 1: return dataframes[0]
        merged = dataframes[0].copy()
        for df in dataframes[1:]:
            try:
                cols = [c for c in df.columns if c != '구분' and not c.endswith('_원시값')]
                for c in cols:
                    merged = merged.set_index('구분').join(df.set_index('구분')[c], how='outer').reset_index()
            except Exception as e:
                st.warning(f"⚠️ 병합 중 오류: {e}")
        return merged.fillna("-")

# --- backward compatibility shim ---
SKFinancialDataProcessor = FinancialDataProcessor
__all__ = ["FinancialDataProcessor", "SKFinancialDataProcessor"]
