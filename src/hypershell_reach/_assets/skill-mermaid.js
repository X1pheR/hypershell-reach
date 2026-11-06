"use strict";
/* Uses the same pinned Mermaid + DOMPurify distributions as HomeSight v40. */
document.addEventListener("DOMContentLoaded", async () => {
  const blocks = Array.from(document.querySelectorAll(".skill-markdown pre.skill-mermaid-fallback code.language-mermaid"));
  if (!blocks.length) return;
  if (!globalThis.mermaid || !globalThis.DOMPurify) return;
  const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  globalThis.mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    theme: "base",
    flowchart: {htmlLabels: false, useMaxWidth: true},
    themeVariables: {
      background: css("--surface"),
      primaryColor: css("--surface-soft"),
      primaryTextColor: css("--heading"),
      primaryBorderColor: css("--accent"),
      secondaryColor: css("--surface-raised"),
      secondaryTextColor: css("--heading"),
      lineColor: css("--muted"),
      textColor: css("--heading"),
      mainBkg: css("--surface-soft"),
      nodeBorder: css("--accent"),
      fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    }
  });
  for (let i = 0; i < blocks.length; i++) {
    const code = blocks[i];
    try {
      const rendered = await globalThis.mermaid.render("reach-skill-mermaid-" + i, code.textContent);
      const safe = globalThis.DOMPurify.sanitize(rendered.svg, {USE_PROFILES: {svg: true, svgFilters: true}});
      if (!safe || !safe.includes("<svg")) throw new Error("Diagram sanitization failed");
      const container = document.createElement("div");
      container.className = "skill-diagram";
      container.setAttribute("role", "img");
      container.setAttribute("aria-label", "Mermaid diagram");
      container.innerHTML = safe;
      code.parentElement.replaceWith(container);
    } catch (_error) {
      // Leave complete escaped source in place rather than silently hide an unsupported diagram.
    }
  }
});
