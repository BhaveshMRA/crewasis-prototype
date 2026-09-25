const pptxgen = require("pptxgenjs");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");
const fa = require("react-icons/fa");

const C = {
  dark: "1A1825", dark2: "2A2540", purple: "7D66EC", lilac: "B9ACF5", tint: "F4F2FD",
  ink: "1A1825", muted: "5F5B72", soft: "D8D4E8", soft2: "C9C4DA",
  green: "059669", amber: "F59E0B", blue: "0284C7", white: "FFFFFF",
};
const HEAD = "Arial", BODY = "Calibri";

async function icon(Comp, color, size = 256) {
  const svg = ReactDOMServer.renderToStaticMarkup(React.createElement(Comp, { color: "#" + color, size }));
  const png = await sharp(Buffer.from(svg)).png().toBuffer();
  return "image/png;base64," + png.toString("base64");
}

async function iconCircle(slide, Comp, x, y, d, fill, fg) {
  slide.addShape("ellipse", { x, y, w: d, h: d, fill: { color: fill }, line: { color: fill } });
  const pad = d * 0.25;
  slide.addImage({ data: await icon(Comp, fg), x: x + pad, y: y + pad, w: d - 2 * pad, h: d - 2 * pad });
}

function text(slide, t, o) {
  slide.addText(t, { isTextBox: true, fontFace: BODY, margin: 0, valign: "top", ...o });
}

(async () => {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_16x9"; // 10 x 5.625 in
  pres.title = "CREWASIS · Ask Winston";

  // ------------------------------------------------------------------ slide 1: problem + idea
  const s1 = pres.addSlide();
  s1.background = { color: C.dark };
  text(s1, "TEAM 2 · CASE STUDY: INSTAGRAM", { x: 0.6, y: 0.45, w: 6, h: 0.3, fontSize: 11, color: C.lilac, bold: true, charSpacing: 2 });
  text(s1, "CREWASIS · Ask Winston", { x: 0.6, y: 0.8, w: 8.8, h: 0.7, fontFace: HEAD, fontSize: 40, bold: true, color: C.white });
  text(s1, "From answer to action: Winston's agents find what's wrong, prove it with source rows, and hand each team its next move.",
    { x: 0.6, y: 1.6, w: 8.6, h: 0.7, fontSize: 16, color: C.soft });

  text(s1, "The problem", { x: 0.6, y: 2.55, w: 4.4, h: 0.35, fontFace: HEAD, fontSize: 18, bold: true, color: C.white });
  const probs = [
    [fa.FaPuzzlePiece, "Signals are scattered", "Reviews, Instagram, Reddit, competitor pages and orders sit in different places, and small brands have no analyst to connect them."],
    [fa.FaUsers, "One feed for every role", "Like Instagram treating a shared account as one person, insights reach every team the same way, so nobody owns them."],
  ];
  for (let i = 0; i < probs.length; i++) {
    const y = 3.05 + i * 1.0;
    await iconCircle(s1, probs[i][0], 0.6, y, 0.5, C.purple, C.white);
    text(s1, probs[i][1], { x: 1.3, y: y - 0.02, w: 3.8, h: 0.3, fontSize: 14, bold: true, color: C.white });
    text(s1, probs[i][2], { x: 1.3, y: y + 0.28, w: 3.8, h: 0.62, fontSize: 11.5, color: C.soft2 });
  }

  const stats = [["−20%", "bar sales this quarter"], ["38% → 29%", "repeat purchase"], ["400", "regular buyers stopped ordering"]];
  stats.forEach(([v, l], i) => {
    const y = 2.55 + i * 0.83;
    s1.addShape("roundRect", { x: 5.55, y, w: 3.85, h: 0.7, fill: { color: C.dark2 }, line: { color: C.dark2 }, rectRadius: 0.08 });
    text(s1, v, { x: 5.75, y: y + 0.12, w: 1.75, h: 0.46, fontFace: HEAD, fontSize: 24, bold: true, color: C.white, valign: "middle" });
    text(s1, l, { x: 7.55, y: y + 0.12, w: 1.75, h: 0.46, fontSize: 12, color: C.soft, valign: "middle" });
  });
  text(s1, "Demo: ProForge Nutrition, a fictional protein brand · only the input data is synthetic",
    { x: 5.55, y: 5.05, w: 3.85, h: 0.25, fontSize: 9.5, italic: true, color: "8F8AA3" });
  s1.addNotes("Problem: a protein brand's sales fell 20% and repeat purchase dropped from 38% to 29%. The clues are everywhere, reviews, Instagram, Reddit, competitor pages, orders, but no one connects them. And when an insight does surface, every team sees it the same way, which is Instagram's shared-account blind spot. Ask Winston closes both gaps: diagnose with evidence, then hand each of five teams its own next move.");

  // ------------------------------------------------------------------ slide 2: how it works
  const s2 = pres.addSlide();
  s2.background = { color: C.white };
  text(s2, "How it works: LLM agents write, code agents check", { x: 0.6, y: 0.4, w: 8.8, h: 0.6, fontFace: HEAD, fontSize: 28, bold: true, color: C.ink });

  const steps = [
    [fa.FaCommentDots, "1 · Ask", "The brand asks Winston its problem in plain words."],
    [fa.FaRoute, "2 · Plan", "Planner (LLM) picks which of 6 lenses to run."],
    [fa.FaCalculator, "3 · Analyze", "Analyst (code) computes every number from 7,701 rows."],
    [fa.FaFileAlt, "4 · Brief", "Writer (LLM) writes it; Governance checks every line."],
    [fa.FaUserCheck, "5 · Act", "Router + Framer give each team its card."],
  ];
  const sw = 1.6, gap = 0.2, top = 1.25, d = 0.6;
  for (let i = 0; i < steps.length; i++) {
    const x = 0.6 + i * (sw + gap), cx = x + sw / 2;
    await iconCircle(s2, steps[i][0], cx - d / 2, top, d, C.purple, C.white);
    if (i < steps.length - 1) {
      s2.addShape("line", { x: cx + d / 2 + 0.08, y: top + d / 2, w: sw + gap - d - 0.16, h: 0,
        line: { color: C.lilac, width: 1.5, endArrowType: "triangle" } });
    }
    text(s2, steps[i][1], { x, y: top + 0.72, w: sw, h: 0.3, fontSize: 14, bold: true, color: C.ink, align: "center" });
    text(s2, steps[i][2], { x, y: top + 1.03, w: sw, h: 0.75, fontSize: 11, color: C.muted, align: "center" });
  }

  const py = 3.2, ph = 1.9;
  s2.addShape("roundRect", { x: 0.6, y: py, w: 4.25, h: ph, fill: { color: C.tint }, line: { color: C.tint }, rectRadius: 0.1 });
  text(s2, "6 lenses on the problem", { x: 0.85, y: py + 0.2, w: 3.8, h: 0.3, fontSize: 14, bold: true, color: C.ink });
  const lenses = ["Where the product fails", "Competitor price & claims", "Customer discovery", "Sales channels", "Retention playbook", "Where buyers talk"];
  lenses.forEach((l, i) => {
    const x = 0.85 + (i % 2) * 1.92, y = py + 0.62 + Math.floor(i / 2) * 0.37;
    s2.addShape("roundRect", { x, y, w: 1.8, h: 0.3, fill: { color: C.white }, line: { color: C.lilac, width: 0.75 }, rectRadius: 0.15 });
    text(s2, l, { x, y, w: 1.8, h: 0.3, fontSize: 10.5, color: C.ink, align: "center", valign: "middle" });
  });

  s2.addShape("roundRect", { x: 5.15, y: py, w: 4.25, h: ph, fill: { color: C.tint }, line: { color: C.tint }, rectRadius: 0.1 });
  text(s2, "Guardrails", { x: 5.4, y: py + 0.2, w: 3.8, h: 0.3, fontSize: 14, bold: true, color: C.ink });
  const guards = [
    [fa.FaLink, "Every sentence cites its source rows"],
    [fa.FaSearch, "Wrong numbers go back to the LLM, else flagged"],
    [fa.FaShieldAlt, "Posts, spend and price changes need Strategy's OK"],
    [fa.FaHourglassHalf, "Thin evidence is held for Insights to confirm"],
    [fa.FaPlug, "LLM down? Winston says so, never template text"],
  ];
  for (let i = 0; i < guards.length; i++) {
    const y = py + 0.58 + i * 0.26;
    await iconCircle(s2, guards[i][0], 5.4, y, 0.22, C.purple, C.white);
    text(s2, guards[i][1], { x: 5.72, y: y - 0.01, w: 3.55, h: 0.25, fontSize: 10, color: C.ink, valign: "middle" });
  }
  s2.addNotes("Five steps. The key design choice: the LLM (Nemotron via Ollama) only plans and words things; Python calculates every number from the source rows. Seven agents: Planner, Writer and Framer are LLM agents; Analyst, Playbook, Governance and Router are code. Every sentence must cite its rows and use only numbers from them; a failing sentence goes back to the LLM with the reason, and if it still fails it is shown flagged as not verified. Anything public, paid or price-related waits for Strategy's approval, even if the LLM rewords it. Evidence with fewer than 8 observations is held for Insights. If the LLM is down, Winston shows an error instead of template text. The Agent trace tab shows every step.");

  // ------------------------------------------------------------------ slide 3: results
  const s3 = pres.addSlide();
  s3.background = { color: C.white };
  text(s3, "ProForge: from diagnosis to owned actions", { x: 0.6, y: 0.4, w: 8.8, h: 0.6, fontFace: HEAD, fontSize: 28, bold: true, color: C.ink });

  s3.addChart(pres.charts.BAR, [{
    name: "1–2★ reviews", labels: ["Texture", "Price", "Taste", "Packaging", "Digestion", "Delivery", "Other"],
    values: [17, 8, 7, 6, 5, 4, 3],
  }], {
    x: 0.5, y: 1.1, w: 4.4, h: 3.05, barDir: "bar", catAxisOrientation: "maxMin",
    chartColors: [C.purple], showValue: true, dataLabelPosition: "outEnd", dataLabelFontSize: 10, dataLabelColor: C.ink,
    showTitle: true, title: "What this quarter's 50 bad reviews complain about", titleFontSize: 12, titleColor: C.ink, titleFontFace: BODY,
    catAxisLabelColor: C.muted, catAxisLabelFontSize: 10, valAxisHidden: true,
    valGridLine: { style: "none" }, catGridLine: { style: "none" }, showLegend: false, barGapWidthPct: 45,
  });
  text(s3, [
    { text: "34% say chalky or dry", options: { bold: true, color: C.purple, fontSize: 15 } },
    { text: ", up from 22.5% last quarter, and it's the top complaint of buyers who stopped reordering.", options: { color: C.muted, fontSize: 11.5 } },
  ], { x: 0.6, y: 4.2, w: 4.3, h: 0.55 });

  text(s3, "Each team gets its move", { x: 5.2, y: 1.15, w: 4.2, h: 0.3, fontSize: 14, bold: true, color: C.ink });
  const acts = [
    ["R&D", C.green, C.white, "Test a softer bar base", "Texture: 41% of lapsed buyers' reviews", false],
    ["Marketing", C.purple, C.white, "Reorder reminder on day 22", "58% of lapsed buyers missed day 24", false],
    ["Marketing", C.purple, C.white, "Answer 22 open sugar questions", "on r/IndianFitness", true],
    ["Innovation", "DB2777", C.white, "Scope a plant-protein bar", "Plant-protein requests up from 12 to 22", false],
    ["Strategy", C.amber, C.dark, "Price a subscribe-and-save box", "Subscribers reorder 71% vs 24%", false],
  ];
  acts.forEach(([team, fill, fg, act, why, gated], i) => {
    const y = 1.55 + i * 0.64;
    s3.addShape("roundRect", { x: 5.2, y, w: 4.2, h: 0.56, fill: { color: C.tint }, line: { color: C.tint }, rectRadius: 0.08 });
    s3.addShape("roundRect", { x: 5.33, y: y + 0.14, w: 1.0, h: 0.28, fill: { color: fill }, line: { color: fill }, rectRadius: 0.14 });
    text(s3, team, { x: 5.33, y: y + 0.14, w: 1.0, h: 0.28, fontSize: 9.5, bold: true, color: fg, align: "center", valign: "middle" });
    text(s3, act, { x: 6.45, y: y + 0.05, w: 2.9, h: 0.25, fontSize: 11.5, bold: true, color: C.ink });
    text(s3, why + (gated ? "  ·  needs approval" : ""), { x: 6.45, y: y + 0.3, w: 2.9, h: 0.22, fontSize: 9.5, color: gated ? "B45309" : C.muted });
  });
  text(s3, "Learns per team: Not relevant ×0.8 · improved ×1.1 · worse ×0.9",
    { x: 0.6, y: 4.92, w: 4.3, h: 0.4, fontSize: 9.5, color: C.purple, bold: true });
  text(s3, "Next: live connectors · measured outcomes · referrals",
    { x: 5.2, y: 4.92, w: 4.2, h: 0.4, fontSize: 9.5, color: C.muted });
  s3.addNotes("In the demo, the FDE finds that texture is the top complaint (34% of bad reviews, up from 22.5%) and the main reason regular buyers stopped. Each team gets its own move: R&D fixes the texture, Marketing sends a reorder reminder on day 22 and answers the 22 open sugar questions on r/IndianFitness (which needs Strategy's approval because it's public), Innovation scopes a plant-protein bar from the same evidence Insights validates, and Strategy decides on subscribe-and-save. Teams mark cards Not relevant and log whether an action worked, and that re-ranks only their own board. Next steps: live data connectors, outcomes measured automatically, and a refer-a-brand growth loop.");

  await pres.writeFile({ fileName: "CREWASIS_Ask_Winston.pptx" });
  console.log("written");
})();
