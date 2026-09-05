# -*- coding: utf-8 -*-
"""
통합 보고서 생성 모듈 (kaleido/plotly 제거 → matplotlib 기반 차트 삽입)
필요 패키지: pip install reportlab pandas openpyxl matplotlib
"""

import io
import os
import sys
import traceback
import pandas as pd
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import (
    Paragraph, Table, TableStyle, Spacer, PageBreak, Image as RLImage, SimpleDocTemplate
)
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import matplotlib.pyplot as plt

# Optional OpenAI (GPT) integration
try:
    from openai import OpenAI
    GPT_AVAILABLE = True
except Exception:
    GPT_AVAILABLE = False
    # print("⚠️ OpenAI 패키지가 없습니다. GPT 기능을 사용하려면 'pip install openai'를 실행하세요.")


# --------------------------
# 폰트 등록 관련 유틸
# --------------------------
def get_font_paths():
    """
    환경에 맞춰 실제 ttf 경로를 설정하세요.
    예: 폰트 파일들을 프로젝트의 'fonts/' 폴더에 넣고 경로를 지정.
    """
    font_paths = {
        "Korean": "fonts/NanumGothic.ttf",
        "KoreanBold": "fonts/NanumGothicBold.ttf",
        "KoreanSerif": "fonts/NanumMyeongjo.ttf"
    }
    # 사용 환경에 맞춰 경로 변경 권장
    return font_paths


def register_fonts_safe():
    """
    안전하게 폰트를 등록하고 사용 가능한 폰트 이름을 반환
    fallback으로 기본 라틴 폰트 사용.
    """
    font_paths = get_font_paths()
    registered_fonts = {}
    default_fonts = {
        "Korean": "Helvetica",
        "KoreanBold": "Helvetica-Bold",
        "KoreanSerif": "Times-Roman"
    }

    for key, path in font_paths.items():
        try:
            if os.path.exists(path) and os.path.getsize(path) > 0:
                name = key
                if name not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(name, path))
                registered_fonts[name] = name
                # print(f"✅ 폰트 등록 성공: {name} -> {path}")
            else:
                # print(f"⚠️ 폰트 파일 누락 또는 비어있음: {path}")
                registered_fonts[key] = default_fonts.get(key, "Helvetica")
        except Exception as e:
            # print(f"⚠️ 폰트 등록 실패 ({key}): {e}")
            registered_fonts[key] = default_fonts.get(key, "Helvetica")

    # ensure keys exist
    if 'Korean' not in registered_fonts:
        registered_fonts['Korean'] = default_fonts['Korean']
    if 'KoreanBold' not in registered_fonts:
        registered_fonts['KoreanBold'] = default_fonts['KoreanBold']
    if 'KoreanSerif' not in registered_fonts:
        registered_fonts['KoreanSerif'] = default_fonts['KoreanSerif']

    return registered_fonts


# --------------------------
# 안전 변환 및 텍스트 전처리
# --------------------------
def safe_str_convert(value):
    """안전하게 값을 문자열로 변환"""
    try:
        if pd.isna(value):
            return ""
        return str(value)
    except Exception:
        return ""


def clean_ai_text(raw):
    """
    AI 인사이트 텍스트 정리 (단순 구현)
    반환: list of (type, line) where type in {'title','body','text'}
    인풋에서 '|' 포함 라인은 ASCII 테이블로 간주해서 별도 처리 가능
    """
    try:
        if not raw or pd.isna(raw):
            return []
        raw_str = str(raw).strip()
        if not raw_str:
            return []

        # 간단한 마킹 제거
        import re
        raw_str = re.sub(r'[*_~]+', '', raw_str)
        blocks = []
        for line in raw_str.splitlines():
            line = line.strip()
            if not line:
                continue
            # 제목 판단 (예: 시작에 숫자 또는 '#' 또는 '###' 등)
            if line.startswith('###') or line.startswith('##') or line.startswith('#'):
                blocks.append(('title', line.lstrip('#').strip()))
            elif re.match(r'^\d+(\.|:)\s', line) or re.match(r'^\d+\)', line):
                blocks.append(('title', line))
            else:
                blocks.append(('body', line))
        return blocks
    except Exception as e:
        # print(f"❌ AI 텍스트 정리 오류: {e}")
        return []


# --------------------------
# ASCII 표 → reportlab Table
# --------------------------
def ascii_to_table(lines, registered_fonts, header_color='#E31E24', row_colors=None):
    """ASCII 표를 reportlab 테이블로 변환"""
    try:
        if not lines or len(lines) < 1:
            return None

        # 첫 줄을 헤더로 간주
        header = [c.strip() for c in lines[0].split('|') if c.strip()]
        if not header:
            return None

        data = []
        # 이후 줄들을 데이터로 파싱 (빈열 제외)
        for ln in lines[1:]:
            cols = [c.strip() for c in ln.split('|') if c.strip() or c == '']
            # 만약 cols 길이가 header와 다른 경우 유연하게 채움
            if len(cols) != len(header):
                # 간단 보정: 짧으면 빈 칸 채우기, 길면 자르기
                if len(cols) < len(header):
                    cols.extend([''] * (len(header) - len(cols)))
                else:
                    cols = cols[:len(header)]
            data.append(cols)

        if not data:
            return None

        if row_colors is None:
            row_colors = [colors.whitesmoke, colors.HexColor('#F7F7F7')]

        tbl = Table([header] + data)
        tbl.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor(header_color)),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), registered_fonts.get('KoreanBold', 'Helvetica-Bold')),
            ('FONTNAME', (0, 1), (-1, -1), registered_fonts.get('Korean', 'Helvetica')),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), row_colors),
        ]))
        return tbl
    except Exception as e:
        # print(f"❌ ASCII 테이블 변환 오류: {e}")
        return None


# --------------------------
# DataFrame 분할 (PDF에 맞춤)
# --------------------------
def split_dataframe_for_pdf(df, max_rows_per_page=20, max_cols_per_page=8):
    """
    DataFrame을 행/열 단위로 분할해서 PDF에 적합한 청크 리스트 반환
    각 청크는 dict: {'data': chunk_df, 'row_range':(...), 'col_range':(...)}
    """
    try:
        if df is None or df.empty:
            return []

        chunks = []
        total_rows = len(df)
        total_cols = len(df.columns)

        for row_start in range(0, total_rows, max_rows_per_page):
            row_end = min(row_start + max_rows_per_page, total_rows)
            row_chunk = df.iloc[row_start:row_end]

            for col_start in range(0, total_cols, max_cols_per_page):
                col_end = min(col_start + max_cols_per_page, total_cols)
                col_names = df.columns[col_start:col_end]
                chunk = row_chunk[col_names]

                chunk_info = {
                    'data': chunk,
                    'row_range': (row_start, row_end - 1),
                    'col_range': (col_start, col_end - 1),
                    'is_last_row_chunk': row_end == total_rows,
                    'is_last_col_chunk': col_end == total_cols
                }
                chunks.append(chunk_info)

        return chunks
    except Exception as e:
        # print(f"❌ DataFrame 분할 오류: {e}")
        return []


# --------------------------
# 테이블 추가 함수
# --------------------------
def add_chunked_table(story, df, title, registered_fonts, BODY_STYLE, header_color='#F2F2F2'):
    """분할된 테이블을 story에 추가"""
    try:
        if df is None or df.empty:
            story.append(Paragraph(f"{title}: 데이터가 없습니다.", BODY_STYLE))
            return

        story.append(Paragraph(title, BODY_STYLE))
        story.append(Spacer(1, 8))

        chunks = split_dataframe_for_pdf(df)

        for i, chunk_info in enumerate(chunks):
            chunk = chunk_info['data']

            if len(chunks) > 1:
                row_info = f"행 {chunk_info['row_range'][0] + 1}~{chunk_info['row_range'][1] + 1}"
                col_info = f"열 {chunk_info['col_range'][0] + 1}~{chunk_info['col_range'][1] + 1}"
                story.append(Paragraph(f"[{row_info}, {col_info}]", BODY_STYLE))

            table_data = [chunk.columns.tolist()]
            for _, row in chunk.iterrows():
                table_data.append([safe_str_convert(val) for val in row.values])

            tbl = Table(table_data, repeatRows=1)
            tbl.setStyle(TableStyle([
                ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor(header_color)),
                ('FONTNAME', (0, 0), (-1, 0), registered_fonts.get('KoreanBold', 'Helvetica-Bold')),
                ('FONTNAME', (0, 1), (-1, -1), registered_fonts.get('Korean', 'Helvetica')),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F8F8F8')]),
            ]))

            story.append(tbl)
            story.append(Spacer(1, 12))

            # 페이지 브레이크 조건 (예시: 2 청크마다)
            if i < len(chunks) - 1 and (i + 1) % 2 == 0:
                story.append(PageBreak())
    except Exception as e:
        # print(f"❌ 테이블 추가 오류 ({title}): {e}")
        story.append(Paragraph(f"{title}: 테이블 생성 중 오류가 발생했습니다.", BODY_STYLE))


# --------------------------
# 재무분석 섹션 (matplotlib figure 추가)
# --------------------------
def add_financial_data_section(story, financial_data, quarterly_df, chart_figures, registered_fonts, HEADING_STYLE, BODY_STYLE):
    """재무분석 결과 섹션 추가 (표 + 차트 이미지)"""
    try:
        story.append(Paragraph("1. 재무분석 결과", HEADING_STYLE))

        # 1-1. 분기별 재무지표 상세 데이터
        if quarterly_df is not None and not quarterly_df.empty:
            add_chunked_table(story, quarterly_df, "1-1. 분기별 재무지표 상세 데이터",
                             registered_fonts, BODY_STYLE, '#E6F3FF')
        else:
            story.append(Paragraph("1-1. 분기별 재무지표 상세 데이터: 데이터가 없습니다.", BODY_STYLE))

        story.append(Spacer(1, 12))

        # 1-2. SK에너지 대비 경쟁사 갭차이 분석표
        if financial_data is not None and not financial_data.empty:
            display_cols = [c for c in financial_data.columns if not str(c).endswith('_원시값')]
            df_display = financial_data[display_cols].copy()
            add_chunked_table(story, df_display, "1-2. SK에너지 대비 경쟁사 갭차이 분석",
                             registered_fonts, BODY_STYLE, '#F2F2F2')
        else:
            story.append(Paragraph("1-2. SK에너지 대비 경쟁사 갭차이 분석: 데이터가 없습니다.", BODY_STYLE))

        # 1-3. matplotlib 차트 이미지들 추가
        if chart_figures and len(chart_figures) > 0:
            story.append(Spacer(1, 12))
            story.append(Paragraph("1-3. 시각화 차트", BODY_STYLE))
            story.append(Spacer(1, 8))

            for i, fig in enumerate(chart_figures, 1):
                try:
                    img_buffer = io.BytesIO()
                    if hasattr(fig, 'savefig'):  # matplotlib Figure
                        fig.savefig(img_buffer, format='png', bbox_inches='tight')
                        plt.close(fig)
                    elif hasattr(fig, 'to_image'):  # plotly Figure (kaleido 필요)
                        img_buffer.write(fig.to_image(format='png', width=900, height=500))
                    else:
                        raise TypeError(f"지원하지 않는 차트 타입: {type(fig)}")
                    img_buffer.seek(0)

                    story.append(Paragraph(f"차트 {i}", BODY_STYLE))
                    img = RLImage(img_buffer, width=500, height=300)
                    story.append(img)
                    story.append(Spacer(1, 16))
                except Exception as e:
                    story.append(Paragraph(f"차트 {i}: 이미지 생성 실패 ({e})", BODY_STYLE))
        else:
            # 차트가 없을 때 안내 문구만 추가 (선택)
            story.append(Paragraph("시각화 차트가 제공되지 않았습니다.", BODY_STYLE))

        story.append(Spacer(1, 18))
    except Exception as e:
        # print(f"❌ 재무분석 섹션 추가 오류: {e}")
        pass


# --------------------------
# AI 인사이트 섹션
# --------------------------
def add_ai_insights_section(story, insights, registered_fonts, BODY_STYLE, header_color='#E31E24'):
    """AI 인사이트 섹션 추가"""
    try:
        if not insights:
            story.append(Paragraph("AI 인사이트가 제공되지 않았습니다.", BODY_STYLE))
            story.append(Spacer(1, 18))
            return

        story.append(Spacer(1, 8))
        blocks = clean_ai_text(insights)
        ascii_buffer = []

        for typ, line in blocks:
            # ASCII 표 라인 포함판단 (파이프 포함)
            if '|' in line:
                ascii_buffer.append(line)
                continue

            if ascii_buffer:
                tbl = ascii_to_table(ascii_buffer, registered_fonts, header_color)
                if tbl:
                    story.append(tbl)
                story.append(Spacer(1, 12))
                ascii_buffer.clear()

            if typ == 'title':
                story.append(Paragraph(f"<b>{line}</b>", BODY_STYLE))
            else:
                story.append(Paragraph(line, BODY_STYLE))

        if ascii_buffer:
            tbl = ascii_to_table(ascii_buffer, registered_fonts, header_color)
            if tbl:
                story.append(tbl)

        story.append(Spacer(1, 18))
    except Exception as e:
        # print(f"❌ AI 인사이트 섹션 추가 오류: {e}")
        pass


# --------------------------
# GPT 기반 전략 제안 (플레이스홀더)
# --------------------------
def generate_strategic_recommendations(insights, financial_data=None, gpt_api_key=None):
    """
    GPT에 연결해 전략 제안 생성하는 함수.
    실제 연동하려면 openai 라이브러리 및 API key 설정 필요.
    현재는 간단한 placeholder를 반환.
    """
    try:
        if not insights:
            return "인사이트가 없어 전략 제안을 생성할 수 없습니다."

        if not GPT_AVAILABLE or not gpt_api_key:
            # placeholder
            return (
                "GPT 연동 정보가 없으므로 예시 전략 제안입니다.\n"
                "1) 단기: 비용 구조 점검 및 변동비 최적화\n"
                "2) 중기: 제품 믹스 개선 및 고부가가치 사업 확대\n"
                "3) 장기: 탈탄소 전환 투자 및 포트폴리오 재구성\n"
            )

        client = OpenAI(api_key=gpt_api_key)
        prompt = f"당신은 에너지 업계 경영 컨설턴트입니다. 다음 인사이트를 바탕으로 실행 가능한 전략 제안을 작성하세요:\n\n{insights}"
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "당신은 에너지 업계 전문 경영 컨설턴트입니다."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1200,
            temperature=0.7
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"전략 제안 생성 중 오류 발생: {e}"


def add_strategic_recommendations_section(story, recommendations, registered_fonts, HEADING_STYLE, BODY_STYLE):
    """전략 제안 섹션 추가"""
    try:
        if not recommendations:
            story.append(Paragraph("GPT 기반 전략 제안을 생성할 수 없습니다.", BODY_STYLE))
            story.append(Spacer(1, 18))
            return

        story.append(Spacer(1, 8))

        blocks = clean_ai_text(recommendations)
        if not blocks:
            # 그냥 전체 텍스트로 삽입
            story.append(Paragraph(recommendations, BODY_STYLE))
        else:
            for typ, line in blocks:
                if typ == 'title':
                    story.append(Paragraph(f"<b>{line}</b>", BODY_STYLE))
                else:
                    story.append(Paragraph(line, BODY_STYLE))

        story.append(Spacer(1, 18))
    except Exception as e:
        # print(f"❌ 전략 제안 섹션 추가 오류: {e}")
        pass


# --------------------------
# 뉴스 섹션
# --------------------------
def add_news_section(story, news_data, insights, registered_fonts, HEADING_STYLE, BODY_STYLE):
    """뉴스 하이라이트 및 종합 분석 섹션 내용 추가 (헤딩 제외)"""
    try:
        if news_data is not None and not news_data.empty:
            story.append(Paragraph("4-1. 최신 뉴스 하이라이트", BODY_STYLE))
            for i, title in enumerate(news_data.get("제목", news_data.columns[0]).head(10), 1):
                story.append(Paragraph(f"{i}. {safe_str_convert(title)}", BODY_STYLE))
            story.append(Spacer(1, 16))
        else:
            story.append(Paragraph("뉴스 데이터가 제공되지 않았습니다.", BODY_STYLE))

        story.append(Spacer(1, 18))
    except Exception as e:
        # print(f"❌ 뉴스 섹션 내용 추가 오류: {e}")
        pass


# --------------------------
# Excel 보고서 생성
# --------------------------
def create_excel_report(financial_data=None, news_data=None, insights=None):
    """Excel 보고서 생성"""
    try:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            if financial_data is not None and not financial_data.empty:
                financial_data.to_excel(writer, sheet_name='재무분석', index=False)
            else:
                pd.DataFrame({'메모': ['재무 데이터가 없습니다.']}).to_excel(writer, sheet_name='재무분석', index=False)

            if news_data is not None and not news_data.empty:
                news_data.to_excel(writer, sheet_name='뉴스분석', index=False)
            else:
                pd.DataFrame({'메모': ['뉴스 데이터가 없습니다.']}).to_excel(writer, sheet_name='뉴스분석', index=False)

            if insights:
                insight_lines = str(insights).split('\n')
                insight_df = pd.DataFrame({'AI 인사이트': insight_lines})
                insight_df.to_excel(writer, sheet_name='AI인사이트', index=False)
            else:
                pd.DataFrame({'메모': ['AI 인사이트가 없습니다.']}).to_excel(writer, sheet_name='AI인사이트', index=False)

        output.seek(0)
        return output.getvalue()

    except Exception as e:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            error_df = pd.DataFrame({
                '오류': [f"Excel 생성 중 오류 발생: {str(e)}"],
                '해결방법': ['시스템 관리자에게 문의해주세요.']
            })
            error_df.to_excel(writer, sheet_name='오류정보', index=False)
        output.seek(0)
        return output.getvalue()


# --------------------------
# PDF 보고서 생성 (메인)
# --------------------------
def create_enhanced_pdf_report(
    financial_data=None,
    news_data=None,
    insights=None,
    chart_figures=None,  # matplotlib Figure 리스트
    quarterly_df=None,
    show_footer=False,
    report_target="SK이노베이션 경영진",
    report_author="보고자 미기재",
    gpt_api_key=None,
    font_paths=None,
):
    """향상된 PDF 보고서 생성 (matplotlib 차트 직접 삽입)"""
    try:
        registered_fonts = register_fonts_safe()

        TITLE_STYLE = ParagraphStyle(
            'Title',
            fontName=registered_fonts.get('KoreanBold', 'Helvetica-Bold'),
            fontSize=20,
            leading=30,
            spaceAfter=15,
            alignment=1,
        )
        HEADING_STYLE = ParagraphStyle(
            'Heading',
            fontName=registered_fonts.get('KoreanBold', 'Helvetica-Bold'),
            fontSize=14,
            leading=23,
            textColor=colors.HexColor('#E31E24'),
            spaceBefore=16,
            spaceAfter=10,
        )
        BODY_STYLE = ParagraphStyle(
            'Body',
            fontName=registered_fonts.get('KoreanSerif', 'Times-Roman'),
            fontSize=12,
            leading=18,
            spaceAfter=6,
        )

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=40, rightMargin=40, topMargin=40, bottomMargin=40)

        story = []

        # 표지
        story.append(Paragraph("손익개선을 위한 SK에너지 및 경쟁사 비교 분석 보고서", TITLE_STYLE))
        story.append(Spacer(1, 20))

        report_info = f"""
        <b>보고일자:</b> {datetime.now().strftime('%Y년 %m월 %d일')}<br/>
        <b>보고대상:</b> {safe_str_convert(report_target)}<br/>
        <b>보고자:</b> {safe_str_convert(report_author)}
        """
        story.append(Paragraph(report_info, BODY_STYLE))
        story.append(Spacer(1, 30))

        # 1. 재무분석 결과 (표 + 차트 이미지)
        add_financial_data_section(story, financial_data, quarterly_df, chart_figures,
                                   registered_fonts, HEADING_STYLE, BODY_STYLE)

        # 2. AI 인사이트
        story.append(Paragraph("2. AI 분석 인사이트", HEADING_STYLE))
        add_ai_insights_section(story, insights, registered_fonts, BODY_STYLE)

        # 3. GPT 기반 전략 제안 (AI 인사이트가 있을 때만)
        if insights:
            strategic_recommendations = generate_strategic_recommendations(insights, financial_data, gpt_api_key)
            story.append(Paragraph("3. SK에너지 전략 제안", HEADING_STYLE))
            add_strategic_recommendations_section(story, strategic_recommendations, registered_fonts, HEADING_STYLE, BODY_STYLE)
        else:
            story.append(Paragraph("AI 인사이트가 없어 전략 제안을 생성하지 않았습니다.", BODY_STYLE))

        # 4. 뉴스 하이라이트 및 종합 분석
        story.append(Paragraph("4. 뉴스 하이라이트 및 종합 분석", HEADING_STYLE))
        add_news_section(story, news_data, insights, registered_fonts, HEADING_STYLE, BODY_STYLE)

        # 푸터 (선택사항)
        if show_footer:
            story.append(Spacer(1, 24))
            footer_text = "※ 본 보고서는 대시보드에서 자동 생성되었습니다."
            story.append(Paragraph(footer_text, BODY_STYLE))

        # 페이지 번호 추가 함수
        def _page_number(canvas, doc):
            try:
                canvas.setFont('Helvetica', 9)
                canvas.drawCentredString(A4[0] / 2, 20, f"- {canvas.getPageNumber()} -")
            except Exception:
                pass

        # 빌드
        doc.build(story, onFirstPage=_page_number, onLaterPages=_page_number)
        buffer.seek(0)
        return buffer.getvalue()

    except Exception as e:
        # fallback: 에러 PDF 생성
        try:
            buffer = io.BytesIO()
            doc = SimpleDocTemplate(buffer, pagesize=A4)
            error_story = [
                Paragraph("보고서 생성 오류", getSampleStyleSheet()['Title']),
                Spacer(1, 20),
                Paragraph(f"오류 내용: {str(e)}", getSampleStyleSheet()['Normal']),
                Spacer(1, 12),
                Paragraph("시스템 관리자에게 문의해주세요.", getSampleStyleSheet()['Normal'])
            ]
            doc.build(error_story)
            buffer.seek(0)
            return buffer.getvalue()
        except Exception:
            raise e


# --------------------------
# 통합 생성 함수
# --------------------------
def generate_report_with_gpt_insights(
    financial_data=None,
    news_data=None,
    insights=None,
    chart_figures=None,  # matplotlib Figure 리스트
    quarterly_df=None,
    gpt_api_key=None,
    **kwargs
):
    """
    matplotlib 차트와 GPT 인사이트를 포함한 완전한 보고서 생성
    
    반환: PDF 바이너리(bytes)
    사용 예:
        pdf_bytes = generate_report_with_gpt_insights(
            financial_data=df_fin,
            news_data=df_news,
            insights=ai_insights_text,
            chart_figures=[fig1, fig2],
            quarterly_df=df_quarterly,
            gpt_api_key="your_openai_key"
        )
    """
    try:
        pdf_bytes = create_enhanced_pdf_report(
            financial_data=financial_data,
            news_data=news_data,
            insights=insights,
            chart_figures=chart_figures,
            quarterly_df=quarterly_df,
            gpt_api_key=gpt_api_key,
            **kwargs
        )
        return pdf_bytes
    except Exception as e:
        raise e


# --------------------------
# 의존성 체크
# --------------------------
def check_dependencies():
    """필요한 패키지들이 설치되어 있는지 체크"""
    missing = []
    try:
        import matplotlib  # noqa
    except Exception:
        missing.append('matplotlib')
    try:
        import reportlab  # noqa
    except Exception:
        missing.append('reportlab')
    try:
        import pandas  # noqa
    except Exception:
        missing.append('pandas')
    try:
        import openpyxl  # noqa
    except Exception:
        missing.append('openpyxl')

    if missing:
        print("다음 패키지를 설치하세요:")
        for m in missing:
            print(f"  pip install {m}")
        return False
    return True


# --------------------------
# 간단 실행 예시 (if __name__ == "__main__")
# --------------------------
if __name__ == "__main__":
    # 의존성 체크
    check_dependencies()

    # 샘플 데이터(간단)
    df_fin = pd.DataFrame({
        '구분': ['매출액', '영업이익', '영업이익률(%)'],
        'SK에너지': [10_000, 800, 8.0],
        'S-Oil': [9_500, 760, 8.0],
        'GS칼텍스': [8_800, 440, 5.0]
    })

    df_quarterly = pd.DataFrame({
        '분기': ['2024Q1', '2024Q2', '2024Q3'],
        '회사': ['SK에너지', 'SK에너지', 'SK에너지'],
        '매출액(조원)': [5.1, 5.8, 6.0],
        '영업이익률(%)': [7.5, 8.0, 8.2]
    })

    # 간단 matplotlib 차트 예시
    fig1, ax1 = plt.subplots(figsize=(6, 3))
    ax1.bar(['SK', 'SOil', 'GS'], [10, 9.5, 8.8])
    ax1.set_title("예시 회사별 매출 (단위: 조원)")

    fig2, ax2 = plt.subplots(figsize=(6, 3))
    ax2.plot([1, 2, 3], [7.5, 8.0, 8.2], marker='o')
    ax2.set_title("예시 분기별 영업이익률")

    # AI 인사이트 예시 텍스트
    ai_insights_example = """
# 핵심 인사이트
1. 매출은 양호하나 영업이익률 개선 여지 존재.
|지표|SK에너지|경쟁사A|경쟁사B|
|----|----:|----:|----:|
|영업이익률(%)|8.0|8.5|7.0|
"""

    # PDF 생성
    pdf_bytes = generate_report_with_gpt_insights(
        financial_data=df_fin,
        news_data=None,
        insights=ai_insights_example,
        chart_figures=[fig1, fig2],
        quarterly_df=df_quarterly,
        gpt_api_key=None
    )

    # 파일로 저장 (테스트용)
    out_path = "sample_report.pdf"
    with open(out_path, "wb") as f:
        f.write(pdf_bytes)
    print(f"PDF 생성 완료: {os.path.abspath(out_path)}")
