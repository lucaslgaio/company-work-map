"""Render the agreed two-page Portuguese proposal; source remains Markdown."""
import argparse
from collections import Counter
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import re

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer


def plain(text):
    text = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", text)
    return text.replace("**", "").replace("`", "").replace("<!-- pagebreak -->", "")


def inline(text):
    text = text.translate(str.maketrans({"\u2011": "-", "\u2013": "-", "\u2014": "-"}))
    result = escape(text)
    result = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", result)
    result = re.sub(r"`([^`]+)`", r'<font name="Courier" size="9.5">\1</font>', result)
    return re.sub(r"\[([^]]+)\]\(([^)]+)\)", lambda m: m[1], result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.read_text(encoding="utf-8")
    if source.count("<!-- pagebreak -->") != 1:
        raise ValueError("Exactly one deliberate two-page section break is required")
    fonts = Path("/System/Library/Fonts/Supplemental")
    for name, filename in (("Arial", "Arial.ttf"), ("Arial-Bold", "Arial Bold.ttf"), ("Arial-Italic", "Arial Italic.ttf")):
        pdfmetrics.registerFont(TTFont(name, str(fonts / filename)))
    pdfmetrics.registerFontFamily("Arial", normal="Arial", bold="Arial-Bold", italic="Arial-Italic", boldItalic="Arial-Bold")
    navy, ink, muted = colors.HexColor("#183446"), colors.HexColor("#202B35"), colors.HexColor("#5A6872")
    base = dict(fontName="Arial", textColor=ink, alignment=TA_LEFT, splitLongWords=True)
    styles = {
        "body": ParagraphStyle("Body", fontSize=11, leading=14.2, spaceAfter=8, **base),
        "title": ParagraphStyle("Title", fontName="Arial-Bold", fontSize=21, leading=24, spaceAfter=15, textColor=navy, keepWithNext=True),
        "heading": ParagraphStyle("Heading", fontName="Arial-Bold", fontSize=12.4, leading=16, spaceBefore=6, spaceAfter=7, textColor=navy, keepWithNext=True),
        "list": ParagraphStyle("List", fontSize=11, leading=14.2, leftIndent=15, firstLineIndent=-15, spaceAfter=7, **base),
        "status": ParagraphStyle("Status", fontSize=9.5, leading=12, spaceAfter=11, textColor=muted, fontName="Arial"),
    }
    story, buffer = [], []
    def flush():
        if buffer:
            text = " ".join(buffer)
            style = "status" if text.startswith("**Rascunho") else "body"
            story.append(Paragraph(inline(text), styles[style]))
            buffer.clear()
    for line in source.splitlines():
        if not line.strip():
            flush()
        elif line.strip() == "<!-- pagebreak -->":
            flush(); story.append(PageBreak())
        elif line.startswith("# "):
            flush(); story.append(Paragraph(inline(line[2:]), styles["title"]))
        elif line.startswith("## "):
            flush(); story.append(Paragraph(inline(line[3:]), styles["heading"]))
        elif re.match(r"^\d+\. ", line):
            flush(); story.append(Paragraph(inline(line), styles["list"]))
        else:
            buffer.append(line.strip())
    flush()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    def footer(canvas, _document):
        width, _height = A4
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#D7E0E4"))
        canvas.line(48, 43, width - 48, 43)
        canvas.setFont("Arial", 8.5)
        canvas.setFillColor(muted)
        canvas.drawString(48, 29, "Piloto de mapeamento do trabalho | Rascunho em validação")
        canvas.drawRightString(width - 48, 29, f"{canvas.getPageNumber()} / 2")
        canvas.restoreState()
    doc = SimpleDocTemplate(str(args.output), pagesize=A4, rightMargin=48, leftMargin=48,
                            topMargin=43, bottomMargin=56, title="Piloto de mapeamento do trabalho da empresa",
                            author="", subject="Proposta para o fundador - rascunho em validação")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    pdf = PdfReader(args.output)
    extracted = "\n".join(page.extract_text() or "" for page in pdf.pages)
    normalize = lambda value: re.findall(r"[\w]+", plain(value).casefold())
    missing = Counter(normalize(source)) - Counter(normalize(extracted))
    receipt = {"source_sha256": sha256(args.source.read_bytes()).hexdigest(),
               "pdf_sha256": sha256(args.output.read_bytes()).hexdigest(), "pages": len(pdf.pages),
               "source_words": len(source.split()), "body_font": "Arial 11 pt, 14.2 pt leading",
               "missing_source_tokens": dict(missing), "visual_check": "pending_png_inspection"}
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False))
    if len(pdf.pages) != 2 or missing:
        raise SystemExit("Proposal must have exactly two pages and retain all source text")


if __name__ == "__main__":
    main()
