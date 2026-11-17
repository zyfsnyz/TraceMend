# ruff: noqa: E501 - 审计报告模板包含较长中文说明，按 Markdown/PDF 语义阅读
import json
import re
from collections import Counter
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import Span, Trace
from .sensitive import redact_sensitive, redact_text
from .serializers import iso

SEVERITY_LABELS = {"high": "高", "medium": "中", "low": "低", "critical": "严重"}
KIND_LABELS = {
    "user": "用户输入",
    "llm": "LLM 推理",
    "tool": "工具调用",
    "tool_result": "工具返回",
    "system": "系统",
    "mcp": "MCP",
}


def text(value: Any, limit: int = 360) -> str:
    if value is None or value == "":
        return "—"
    if not isinstance(value, str):
        value = json.dumps(redact_sensitive(value), ensure_ascii=False, default=str)
    value = redact_text(value).replace("\r\n", "\n").strip()
    if len(value) > limit:
        return value[:limit] + "…"
    return value or "—"


def markdown_text(value: Any, limit: int = 360) -> str:
    """Escape untrusted audit evidence so Markdown cannot load remote images or links."""
    escaped = escape(text(value, limit))
    escaped = re.sub(r"([\\`*_{}\[\]()#+!|])", r"\\\1", escaped)
    return escaped.replace("\r\n", "\n").replace("\n", "<br>")


def markdown_cell(value: Any, limit: int = 360) -> str:
    return markdown_text(value, limit)


def markdown_code(value: Any) -> str:
    return (
        str(value)
        .replace("`", "'")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def fmt_duration(value: Any) -> str:
    if value is None:
        return "—"
    value = float(value)
    if value < 1000:
        return f"{value:.0f} ms"
    if value < 60_000:
        return f"{value / 1000:.2f} s"
    return f"{value / 60_000:.1f} min"


def generated_at() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def risk_summary(trace: Trace) -> str:
    counts = Counter(finding.severity for finding in trace.findings)
    if not counts:
        return "未发现规则或模型风险线索"
    return "、".join(
        f"{SEVERITY_LABELS.get(severity, severity)}风险 {counts[severity]} 项"
        for severity in ("critical", "high", "medium", "low")
        if counts[severity]
    )


def span_summary(span: Span, limit: int = 220) -> str:
    for value in (span.content, span.tool_result, span.output, span.error):
        if value not in (None, "", [], {}):
            return text(value, limit)
    if span.tool_arguments is not None:
        return text(span.tool_arguments, limit)
    return "—"


def comment_lines(trace: Trace, *, markdown: bool = True) -> list[str]:
    if not trace.comments:
        return ["暂无审计评论。"]
    lines = []
    for comment in trace.comments:
        state = "已解决" if comment.status == "resolved" else "待处理"
        if markdown:
            scope = (
                f"Span {markdown_text(comment.span_id, 120)}"
                if comment.span_id
                else "整条链路"
            )
            lines.append(
                f"- **{markdown_text(comment.author, 80)}**（{state}，{scope}，"
                f"{iso(comment.created_at)}）：{markdown_text(comment.body, 1200)}"
            )
        else:
            scope = f"Span {text(comment.span_id, 120)}" if comment.span_id else "整条链路"
            lines.append(
                f"- {text(comment.author, 80)}（{state}，{scope}，"
                f"{iso(comment.created_at)}）：{text(comment.body, 1200)}"
            )
    return lines


def render_markdown(trace: Trace) -> str:
    severity = Counter(finding.severity for finding in trace.findings)
    lines = [
        "# TraceMend 审计报告",
        "",
        f"> 生成时间：{generated_at()}　|　数据来源：本地 TraceMend 自托管实例",
        "",
        "## 1. 链路概览",
        "",
        "| 字段 | 内容 |",
        "| --- | --- |",
        f"| 链路名称 | {markdown_cell(trace.name)} |",
        f"| Trace ID | `{markdown_code(trace.id)}` |",
        f"| 来源 / 模型 | {markdown_cell(trace.source)} / {markdown_cell(trace.model)} |",
        f"| 状态 | {markdown_cell(trace.status)} |",
        f"| 开始时间 | {iso(trace.started_at) or '—'} |",
        f"| 端到端耗时 | {fmt_duration(trace.duration_ms)} |",
        f"| Token 用量 | {trace.total_tokens or 0}（输入 {trace.prompt_tokens or 0} / 输出 {trace.completion_tokens or 0}） |",
        "",
        "## 2. 执行摘要",
        "",
        f"- 共记录 **{len(trace.spans)}** 个执行步骤，其中工具调用 "
        f"**{sum(span.kind == 'tool' for span in trace.spans)}** 次。",
        f"- 共发现 **{len(trace.findings)}** 项风险线索，"
        f"其中高/严重 **{severity['high'] + severity['critical']}** 项、"
        f"中 **{severity['medium']}** 项、低 **{severity['low']}** 项。",
        f"- 风险分布：{risk_summary(trace)}",
        f"- 审计评论：**{len(trace.comments)}** 条。",
        "",
        "## 3. 风险发现",
        "",
    ]
    if not trace.findings:
        lines.append("未发现风险线索。该结论仅代表当前规则与可选模型检查的结果。")
    else:
        for index, finding in enumerate(trace.findings, 1):
            lines.extend(
                [
                    f"### 3.{index} [{SEVERITY_LABELS.get(finding.severity, finding.severity)}] {markdown_text(finding.title, 160)}",
                    "",
                    f"- **类别**：{markdown_text(finding.category, 80)}",
                    f"- **来源**：{'模型二阶检测' if finding.source == 'llm' else '规则检测'}（`{markdown_code(finding.rule_code)}`）",
                    f"- **证据位置**：{f'`{markdown_code(finding.span_id)}`' if finding.span_id else '整条链路'}",
                    f"- **解释**：{markdown_text(finding.explanation, 1600)}",
                    f"- **证据摘录**：{markdown_text(finding.evidence, 1000)}",
                    "",
                ]
            )
    lines.extend(
        [
            "## 4. 执行时间线",
            "",
            "| # | 类型 | 名称 | 状态 | 耗时 | Token | 摘要 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for span in trace.spans:
        lines.append(
            f"| {span.sequence} | {KIND_LABELS.get(span.kind, span.kind)} | "
            f"{markdown_cell(span.name, 80)} | {markdown_cell(span.status, 40)} | "
            f"{fmt_duration(span.duration_ms)} | {span.total_tokens or 0} | "
            f"{markdown_cell(span_summary(span), 220)} |"
        )
    if not trace.spans:
        lines.append("| — | — | 无执行步骤 | — | — | 0 | — |")
    lines.extend(["", "## 5. 工具调用链", ""])
    tool_spans = [span for span in trace.spans if span.kind in {"tool", "tool_result", "mcp"}]
    if not tool_spans:
        lines.append("本链路没有工具调用。")
    else:
        for span in tool_spans:
            lines.extend(
                [
                    f"### {span.sequence}. {markdown_text(span.name or span.tool_name, 100)}",
                    "",
                    f"- **工具**：`{markdown_code(span.tool_name or '未命名')}`",
                    f"- **状态**：{markdown_text(span.status, 40)}",
                    f"- **参数**：{markdown_text(span.tool_arguments, 800)}",
                    f"- **返回/错误**：{markdown_text(span.tool_result if span.tool_result is not None else span.error, 1200)}",
                    "",
                ]
            )
    lines.extend(
        ["## 6. 审计评论与处理状态", "", *comment_lines(trace), "", "## 7. 证据与局限", ""]
    )
    lines.extend(
        [
            "- 本报告由中国本地部署的 TraceMend 自动生成，原始 Trace、Span 与评论保留在本地数据库。",
            "- 一阶规则检测基于参数结构、工具状态、引用完整性和数值支撑等可解释线索，可能产生误报，需要人工复核。",
            "- 二阶 LLM 检测为可选增值能力，仅在维护者显式开启后运行；其结论同样属于线索，不等同于事实认定。",
            "- 报告中已对常见认证头、API Key 等敏感值做脱敏；仍建议在对外分享前按组织制度复核。",
            "- 本报告用于工程复盘、测试归档与合规留痕，不构成事实或法律结论。",
            "",
            f"_TraceMend · Trace `{markdown_code(trace.id)}` · {generated_at()}_",
        ]
    )
    return "\n".join(lines) + "\n"


_FONT = "TraceMendCN"
_FONT_PATH = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "NotoSansSC-VF.ttf"


def _registered_font() -> str:
    if _FONT not in pdfmetrics.getRegisteredFontNames():
        if _FONT_PATH.exists():
            pdfmetrics.registerFont(TTFont(_FONT, str(_FONT_PATH)))
        else:
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
            return "STSong-Light"
    return _FONT


def _pdf_styles():
    font_name = _registered_font()
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "TraceMendCnTitle",
            parent=base["Title"],
            fontName=font_name,
            fontSize=20,
            leading=27,
            textColor=colors.HexColor("#0f172a"),
        ),
        "h2": ParagraphStyle(
            "TraceMendCnH2",
            parent=base["Heading2"],
            fontName=font_name,
            fontSize=13,
            leading=19,
            spaceBefore=10,
            spaceAfter=5,
            textColor=colors.HexColor("#0f766e"),
        ),
        "body": ParagraphStyle(
            "TraceMendCnBody",
            parent=base["BodyText"],
            fontName=font_name,
            fontSize=9,
            leading=14,
            alignment=TA_LEFT,
        ),
        "small": ParagraphStyle(
            "TraceMendCnSmall", parent=base["BodyText"], fontName=font_name, fontSize=7.5, leading=10.5
        ),
    }


def pdf_paragraph(value: Any, style, limit: int = 1200) -> Paragraph:
    return Paragraph(escape(text(value, limit)).replace("\n", "<br/>"), style)


def render_pdf(trace: Trace) -> bytes:
    styles = _pdf_styles()
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=f"TraceMend 审计报告 - {trace.name}",
        author="TraceMend",
    )
    story: list[Any] = [
        Paragraph("TraceMend 审计报告", styles["title"]),
        Paragraph(f"生成时间：{generated_at()}　|　Trace：{escape(trace.id)}", styles["small"]),
        Spacer(1, 5 * mm),
        Paragraph("1. 链路概览", styles["h2"]),
    ]
    overview = [
        ["字段", "内容"],
        ["链路名称", text(trace.name, 200)],
        ["来源 / 模型", f"{text(trace.source, 80)} / {text(trace.model, 80)}"],
        ["状态", text(trace.status, 40)],
        ["开始时间", iso(trace.started_at) or "—"],
        ["耗时", fmt_duration(trace.duration_ms)],
        [
            "Token",
            f"{trace.total_tokens or 0}（入 {trace.prompt_tokens or 0} / 出 {trace.completion_tokens or 0}）",
        ],
    ]
    table = Table(
        [[Paragraph(escape(str(cell)), styles["small"]) for cell in row] for row in overview],
        colWidths=[35 * mm, 135 * mm],
        repeatRows=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.extend([table, Paragraph("2. 执行摘要", styles["h2"])])
    severity = Counter(finding.severity for finding in trace.findings)
    for line in (
        f"执行步骤 {len(trace.spans)} 个；工具调用 {sum(span.kind == 'tool' for span in trace.spans)} 次。",
        f"风险线索 {len(trace.findings)} 项（高/严重 {severity['high'] + severity['critical']}、中 {severity['medium']}、低 {severity['low']}）。",
        f"风险分布：{risk_summary(trace)}。审计评论 {len(trace.comments)} 条。",
    ):
        story.append(pdf_paragraph(line, styles["body"]))
    story.append(Paragraph("3. 风险发现", styles["h2"]))
    if not trace.findings:
        story.append(
            pdf_paragraph(
                "未发现风险线索。该结论仅代表当前规则与可选模型检查的结果。", styles["body"]
            )
        )
    for finding in trace.findings:
        story.append(
            pdf_paragraph(
                f"[{SEVERITY_LABELS.get(finding.severity, finding.severity)}] {finding.title}",
                styles["body"],
                200,
            )
        )
        story.append(
            pdf_paragraph(
                f"类别：{finding.category}　来源：{'模型二阶检测' if finding.source == 'llm' else '规则检测'}"
                f"　位置：{finding.span_id or '整条链路'}\n"
                f"解释：{finding.explanation}\n证据：{text(finding.evidence, 900)}",
                styles["small"],
                1600,
            )
        )
        story.append(Spacer(1, 2 * mm))
    story.append(Paragraph("4. 执行时间线", styles["h2"]))
    rows = [["#", "类型", "名称", "摘要", "耗时"]]
    for span in trace.spans:
        rows.append(
            [
                str(span.sequence),
                KIND_LABELS.get(span.kind) or span.kind or "未知",
                text(span.name, 80),
                span_summary(span, 300),
                fmt_duration(span.duration_ms),
            ]
        )
    if len(rows) == 1:
        rows.append(["—", "—", "无执行步骤", "—", "—"])
    timeline = Table(
        [
            [Paragraph(escape(str(cell)).replace("\n", "<br/>"), styles["small"]) for cell in row]
            for row in rows
        ],
        colWidths=[10 * mm, 25 * mm, 40 * mm, 75 * mm, 20 * mm],
        repeatRows=1,
    )
    timeline.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.extend([timeline, Paragraph("5. 审计评论与处理状态", styles["h2"])])
    for line in comment_lines(trace, markdown=False):
        story.append(pdf_paragraph(line.lstrip("- "), styles["small"], 1200))
    story.extend([PageBreak(), Paragraph("6. 证据与局限", styles["h2"])])
    for line in (
        "本报告由中国本地部署的 TraceMend 自动生成，原始 Trace、Span 与评论保留在本地数据库。",
        "规则检测结果可能产生误报，需要人工复核；二阶 LLM 检测为可选能力，其结论属于线索。",
        "报告中已对常见认证头、API Key 等敏感值做脱敏；对外分享前仍应按组织制度复核。",
        "本报告用于工程复盘、测试归档与合规留痕，不构成事实或法律结论。",
    ):
        story.append(pdf_paragraph(f"• {line}", styles["body"]))
    document.build(story)
    return buffer.getvalue()
