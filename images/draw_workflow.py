"""Draws images/agent_workflow.png (the LangGraph in app/graph.py). Run: python images/draw_workflow.py"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent / "agent_workflow.png"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
S = 2  # supersampling for smooth edges
W, H = 1700, 2240

KINDS = {  # fill, outline
    "io": ("#E8EAF0", "#5B6275"),
    "guard": ("#FDE7E7", "#C0392B"),
    "llm": ("#E3F0FF", "#2B6CB0"),
    "code": ("#E6F6EC", "#2F855A"),
    "db": ("#FFF0DC", "#C77700"),
}
INK, MUTED = "#1F2430", "#5B6275"

img = Image.new("RGB", (W * S, H * S), "white")
d = ImageDraw.Draw(img)


def f(size, bold=False):
    return ImageFont.truetype(BOLD if bold else FONT, size * S)


def text(xy, s, size, color=INK, bold=False, anchor="mm"):
    d.text((xy[0] * S, xy[1] * S), s, font=f(size, bold), fill=color, anchor=anchor)


def box(cx, cy, w, h, title, sub, kind):
    fill, line = KINDS[kind]
    d.rounded_rectangle(((cx - w / 2) * S, (cy - h / 2) * S, (cx + w / 2) * S, (cy + h / 2) * S),
                        radius=16 * S, fill=fill, outline=line, width=3 * S)
    text((cx, cy - 14), title, 24, INK, True)
    text((cx, cy + 16), sub, 16, MUTED)
    return {"l": (cx - w / 2, cy), "r": (cx + w / 2, cy), "t": (cx, cy - h / 2), "b": (cx, cy + h / 2)}


def head(p, direction, color):
    x, y = p
    a = 10
    pts = {"down": [(x, y), (x - a, y - 2 * a), (x + a, y - 2 * a)],
           "right": [(x, y), (x - 2 * a, y - a), (x - 2 * a, y + a)],
           "left": [(x, y), (x + 2 * a, y - a), (x + 2 * a, y + a)]}[direction]
    d.polygon([(px * S, py * S) for px, py in pts], fill=color)


def line(points, color=MUTED, width=3, dash=False):
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        d.line((x1 * S, y1 * S, x2 * S, y2 * S), fill=color, width=width * S)


def arrow(points, direction="down", color=MUTED, label=None, lpos=None, dash=False):
    line(points, color)
    head(points[-1], direction, color)
    if label:
        text(lpos, label, 15, color, True)


CX, BW, BH, GAP = 640, 560, 84, 40
names = [
    ("question", "User question", "POST /analyze  ·  CLI  ·  Streamlit", "io"),
    ("input_guard", "1. input_guard", "keyword rules first, then fast LLM · fails closed", "guard"),
    ("understand", "2. understand", "fast LLM: intent, metrics, entities · time phrase", "llm"),
    ("retrieve", "3. retrieve  (RAG)", "ChromaDB + fastembed: KPI docs, policies, schema", "db"),
    ("plan", "4. plan", "strong LLM: 1 query (diagnose: up to 3)", "llm"),
    ("generate_sql", "5. generate_sql", "strong LLM → SQL (untrusted)", "llm"),
    ("validate", "6. validate", "sqlglot: 1 SELECT · allow-lists · LIMIT · no PII out", "guard"),
    ("execute", "7. execute", "EXPLAIN cost cap · analyst_ro · read-only · 15 s", "db"),
    ("verify", "8. verify", "empty / irrelevant result check", "code"),
    ("analyze", "9. analyze", "Pandas: growth, share, top-N, drivers", "code"),
    ("visualize", "10. visualize", "Plotly spec built by our code (chart enum)", "code"),
    ("respond", "11. respond", "fast LLM from structured results + ₹ display strings", "llm"),
    ("grounding", "12. grounding_check", "every number must trace to SQL / Pandas", "guard"),
    ("output_guard", "13. output_guard", "no PII values · no SQL errors · no internals", "guard"),
    ("response", "JSON response + request_id", "answer · sql · data · chart · trace · grounded", "io"),
]
top = 150
pos, ports = {}, {}
for i, (key, title, sub, kind) in enumerate(names):
    cy = top + i * (BH + GAP) + BH / 2
    pos[key] = cy
    ports[key] = box(CX, cy, BW, BH, title, sub, kind)

text((W / 2, 52), "Enterprise Data Analyst Agent — agentic workflow", 40, INK, True)
text((W / 2, 98), "LangGraph pipeline in app/graph.py · all LLM output is untrusted and re-checked by code", 20, MUTED)

# main spine
order = [k for k, *_ in names]
for a, b in zip(order, order[1:]):
    arrow([ports[a]["b"], ports[b]["t"]])

# input_guard -> refusal
RX = 1370
ref = box(RX, pos["input_guard"], 420, BH, "templated refusal → END", "off_topic · unsafe_write · injection · pii", "guard")
arrow([ports["input_guard"]["r"], ref["l"]], "right", "#C0392B", "not analytics", (1065, pos["input_guard"] - 18))

# retrieve -> clarify -> output_guard
cla = box(RX, pos["retrieve"], 420, BH, "clarify", "fast LLM asks a question instead of guessing", "llm")
arrow([ports["retrieve"]["r"], cla["l"]], "right", "#2B6CB0", "needs clarification", (1050, pos["retrieve"] - 18))
oy = pos["output_guard"]
arrow([cla["b"], (RX, oy), ports["output_guard"]["r"]], "left", "#2B6CB0")

# retry loops (left) back to generate_sql
gy = pos["generate_sql"]
for key, lx, label in (("validate", 330, "errors → retry (max 3)"),
                       ("execute", 290, "cost cap / DB error"),
                       ("verify", 250, "empty → retry once")):
    y = pos[key]
    arrow([ports[key]["l"], (lx, y), (lx, gy), ports["generate_sql"]["l"]], "right", "#C77700")
    text((lx - 8, (y + gy) / 2 + (0 if key == "validate" else 0)), label, 14, "#C77700", True) if False else None
text((150, pos["validate"] + 4), "invalid SQL\n→ regenerate with\nvalidator errors", 14, "#C77700", True)
text((150, pos["execute"] + 20), "cost-cap / DB error\n→ regenerate\n(shared budget: 3)", 14, "#C77700", True)
text((150, pos["verify"] + 36), "empty / irrelevant\n→ regenerate once", 14, "#C77700", True)

# grounding loop (right) back to respond
ry = pos["respond"]
gx = 1010
arrow([ports["grounding"]["r"], (gx, pos["grounding"]), (gx, ry), ports["respond"]["r"]], "left", "#C0392B")
text((1190, (ry + pos["grounding"]) / 2), "ungrounded number →\nregenerate once,\nthen return grounded=false", 15, "#C0392B", True)

# error shortcut note
text((1170, pos["response"] - 10), "Any node error → audit log\n→ straight to output_guard\n→ generic error + request_id", 16, MUTED, True, "mm")

# legend
ly = H - 130
text((90, ly), "Legend", 20, INK, True, "lm")
for i, (kind, label) in enumerate((("llm", "LLM node"), ("code", "deterministic code"), ("guard", "guardrail / validator"),
                                   ("db", "database / retrieval"), ("io", "entry / exit"))):
    x = 230 + i * 300
    fill, outline = KINDS[kind]
    d.rounded_rectangle((x * S, (ly - 14) * S, (x + 40) * S, (ly + 14) * S), radius=6 * S, fill=fill, outline=outline, width=3 * S)
    text((x + 52, ly), label, 17, INK, False, "lm")
text((W / 2, H - 60), "Audit log (logs/audit.jsonl) records every request; users only ever see generic errors + request_id.", 17, MUTED)

img = img.resize((W, H), Image.LANCZOS)
img.save(OUT, optimize=True)
print("wrote", OUT)
