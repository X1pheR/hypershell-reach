from __future__ import annotations

REACH_MARK_SVG = """<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 48 48\" role=\"img\" aria-label=\"Hypershell Reach product mark\">
  <path d=\"M17.2 11.5h13.6L35 29.1c1.9-.5 3.8-1.2 5.6-2a2.1 2.1 0 0 1 1.7 3.9C37 33.4 30.9 34.6 24 34.6S11 33.4 5.7 31a2.1 2.1 0 0 1 1.7-3.9c1.8.8 3.7 1.5 5.6 2l4.2-17.6Z\" fill=\"#17233c\" stroke=\"#7185a8\" stroke-width=\"2\" stroke-linejoin=\"round\"/>
  <path d=\"M16 25.2h16\" fill=\"none\" stroke=\"#607692\" stroke-width=\"2\" stroke-linecap=\"round\"/>
  <circle cx=\"17.5\" cy=\"25.2\" r=\"2.35\" fill=\"#ff2093\"/>
  <circle cx=\"24\" cy=\"25.2\" r=\"2.35\" fill=\"#3c6cfe\"/>
  <circle cx=\"30.5\" cy=\"25.2\" r=\"2.35\" fill=\"#67e8f9\"/>
</svg>"""

CSS = r"""
:root {
  color-scheme: dark;
  --page: #050816;
  --surface: #0b1020;
  --surface-raised: #0f172a;
  --surface-soft: #131d31;
  --text: #e2e8f0;
  --heading: #f1f5f9;
  --muted: #94a3b8;
  --border: #4c5c80;
  --border-soft: #27334e;
  --accent: #3c6cfe;
  --accent-strong: #67e8f9;
  --secondary: #ff2093;
  --tertiary: #22d3ee;
  --structural-cyan: #78aeb9;
  --structural-pink: #b97599;
  --success: #6ee7a8;
  --warning: #f3c96b;
  --danger: #ff8585;
  --focus: #67e8f9;
  --active-border: rgb(103 232 249 / 34%);
  --active-surface: linear-gradient(100deg, rgb(255 32 147 / 8%), rgb(60 108 254 / 16%) 55%, rgb(34 211 238 / 8%));
  --scrollbar-track: #070b17;
  --scrollbar-thumb: #33415f;
  --scrollbar-thumb-hover: #506284;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  line-height: 1.5;
  background: var(--page);
  color: var(--text);
}

* { box-sizing: border-box; scrollbar-color: var(--scrollbar-thumb) var(--scrollbar-track); scrollbar-width: thin; }
*::-webkit-scrollbar { width: .7rem; height: .7rem; }
*::-webkit-scrollbar-track { background: var(--scrollbar-track); }
*::-webkit-scrollbar-thumb { border: 2px solid var(--scrollbar-track); border-radius: 999px; background: var(--scrollbar-thumb); }
*::-webkit-scrollbar-thumb:hover { background: var(--scrollbar-thumb-hover); }
html { min-width: 320px; background: var(--page); }
body {
  margin: 0;
  min-width: 320px;
  min-height: 100vh;
  background:
    radial-gradient(circle at 10% -10%, rgb(255 32 147 / 6%) 0, transparent 28rem),
    radial-gradient(circle at 76% -12%, rgb(60 108 254 / 10%) 0, transparent 34rem),
    radial-gradient(circle at 100% 0, rgb(34 211 238 / 6%) 0, transparent 28rem),
    var(--page);
  color: var(--text);
}
a { color: inherit; }
button { font: inherit; cursor: pointer; }
[hidden] { display: none !important; }
:focus-visible { outline: 3px solid var(--focus); outline-offset: 3px; }

.skip-link {
  position: fixed;
  z-index: 1000;
  top: .5rem;
  left: .5rem;
  transform: translateY(-180%);
  padding: .7rem 1rem;
  border-radius: .45rem;
  background: var(--text);
  color: var(--page);
}
.skip-link:focus { transform: translateY(0); }
.sr-only { position: absolute !important; width: 1px !important; height: 1px !important; padding: 0 !important; margin: -1px !important; overflow: hidden !important; clip: rect(0, 0, 0, 0) !important; white-space: nowrap !important; border: 0 !important; }
.muted { color: var(--muted); }

.site-header {
  position: sticky;
  z-index: 20;
  top: 0;
  display: grid;
  grid-template-columns: minmax(13rem, 1fr) auto minmax(13rem, 1fr);
  gap: clamp(.65rem, 1.6vw, 1.4rem);
  align-items: center;
  min-height: 4.5rem;
  padding: .65rem clamp(1rem, 3vw, 2.5rem);
  border-bottom: 1px solid var(--border);
  background: rgb(5 8 22 / 94%);
  backdrop-filter: blur(14px);
}
.navigation-progress {
  position: absolute;
  right: 0;
  bottom: -1px;
  left: 0;
  height: 2px;
  overflow: hidden;
  opacity: 0;
  pointer-events: none;
  transition: opacity 120ms ease;
}
.navigation-progress::after {
  content: "";
  position: absolute;
  top: 0;
  bottom: 0;
  left: -34%;
  width: 34%;
  background: linear-gradient(90deg, transparent, var(--secondary), var(--accent), var(--accent-strong), transparent);
  box-shadow: 0 0 10px rgb(103 232 249 / 35%);
}
html.is-navigating .navigation-progress { opacity: 1; }
html.is-navigating .navigation-progress::after { animation: reach-navigation-progress 900ms ease-in-out infinite; }
@keyframes reach-navigation-progress {
  from { transform: translateX(0); }
  to { transform: translateX(395%); }
}
.header-leading, .brand, .header-actions, .page-heading-row, .section-heading, .mobile-sheet-heading { display: flex; align-items: center; }
.header-leading { min-width: 0; gap: .6rem; }
.brand { min-width: 0; gap: .65rem; color: var(--text); text-decoration: none; }
.brand-mark { width: 2.75rem; height: 2.75rem; flex: 0 0 auto; display: block; }
.brand-copy { display: grid; min-width: 0; line-height: 1.1; }
.brand-copy strong { overflow: hidden; color: var(--heading); text-overflow: ellipsis; white-space: nowrap; letter-spacing: -.02em; }
.brand-copy small { margin-top: .18rem; overflow: hidden; color: var(--structural-cyan); font-size: .72rem; text-overflow: ellipsis; white-space: nowrap; }

.primary-navigation {
  display: inline-flex;
  width: fit-content;
  justify-self: center;
  gap: .12rem;
  padding: .2rem;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: rgb(11 16 32 / 78%);
  box-shadow: inset 0 1px 0 rgb(241 245 249 / 5%);
}
.primary-navigation a {
  display: grid;
  place-items: center;
  min-height: 2.55rem;
  padding: .42rem .72rem;
  border: 1px solid transparent;
  border-radius: 999px;
  color: var(--muted);
  text-decoration: none;
  white-space: nowrap;
  transition: border-color 150ms ease, background 150ms ease, color 150ms ease;
}
.primary-navigation a:hover { border-color: var(--border); background: rgb(19 29 49 / 82%); color: var(--text); }
.primary-navigation a[aria-current="page"] { border-color: var(--active-border); background: var(--active-surface); color: var(--heading); box-shadow: 0 4px 16px rgb(0 0 0 / 18%); }
.header-actions { justify-content: flex-end; gap: .45rem; min-width: 0; }
.mode-badge, .availability-badge {
  display: inline-flex;
  align-items: center;
  gap: .45rem;
  min-height: 2.15rem;
  padding: .3rem .72rem;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--surface-raised);
  color: var(--muted);
  font-size: .82rem;
  font-weight: 650;
  white-space: nowrap;
}
.availability-badge { color: var(--structural-cyan); }
.utility-help, .contextual-help { position: relative; text-decoration: none; }
[data-tooltip]::after { content: attr(data-tooltip); position: absolute; z-index: 80; top: calc(100% + .45rem); left: 50%; transform: translateX(-50%) translateY(-.2rem); width: max-content; max-width: min(12rem, calc(100vw - 1rem)); padding: .35rem .5rem; border: 1px solid var(--border); border-radius: .45rem; background: var(--surface-soft); color: var(--heading); font-size: .78rem; font-weight: 600; line-height: 1.2; opacity: 0; pointer-events: none; transition: opacity 120ms ease, transform 120ms ease; }
[data-tooltip]:hover::after, [data-tooltip]:focus-visible::after { opacity: 1; transform: translateX(-50%) translateY(0); }
.header-actions [data-tooltip]::after, .contextual-help[data-tooltip]::after { right: 0; left: auto; transform: translateY(-.2rem); }
.header-actions [data-tooltip]:hover::after, .header-actions [data-tooltip]:focus-visible::after, .contextual-help[data-tooltip]:hover::after, .contextual-help[data-tooltip]:focus-visible::after { transform: translateY(0); }
.icon-button { display: grid; place-items: center; width: 2.75rem; min-width: 2.75rem; height: 2.75rem; min-height: 2.75rem; padding: 0; border: 1px solid var(--border); border-radius: 999px; background: var(--surface-raised); color: var(--text); }
.icon-button:hover { background: var(--surface-soft); }
.icon-button.mobile-menu-toggle { display: none; }
.ui-icon { width: 1.2rem; height: 1.2rem; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }

main { width: min(calc(100% - 2rem), 92rem); margin-inline: auto; padding-block: clamp(1.25rem, 3vw, 2.4rem) 4rem; }
.page-heading-row { justify-content: space-between; align-items: flex-start; gap: 1.5rem; margin-bottom: 1.35rem; }
h1, h2, h3, h4 { color: var(--heading); line-height: 1.2; }
h1 { margin: 0; font-size: clamp(1.7rem, 2.4vw, 2.35rem); letter-spacing: -.04em; }
h2 { margin: 0; font-size: clamp(1.18rem, 1.8vw, 1.5rem); }
h3 { margin: 0; font-size: 1rem; }
.page-summary { max-width: 74ch; margin: .55rem 0 0; color: var(--muted); }

.overview-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .9rem; }
.destination-card, .panel, .docs-navigation, .docs-article, .docs-toc {
  min-width: 0;
  border: 1px solid var(--border);
  border-radius: 1rem;
  background: rgb(11 16 32 / 94%);
  box-shadow: 0 12px 30px rgb(0 0 0 / 18%);
}
.destination-card { position: relative; display: block; min-height: 9rem; padding: 1.15rem; overflow: hidden; text-decoration: none; }
.destination-card::before, .panel-accent::before { content: ""; position: absolute; inset: 0 0 auto; height: 3px; background: var(--structural-cyan); }
.destination-card:hover { border-color: var(--active-border); background: var(--surface-raised); }
.destination-card h2 { margin-bottom: .45rem; color: var(--structural-cyan); }
.destination-card p { margin: .35rem 0 0; color: var(--muted); }
.destination-card .card-link { display: inline-block; margin-top: 1rem; color: var(--accent-strong); font-size: .86rem; font-weight: 700; }
.overview-metric { display: block; color: var(--heading); font-size: 1.22rem; line-height: 1.25; }
.overview-detail { display: block; margin-top: .5rem; color: var(--muted); font-size: .8rem; }
.destination-card.state-warning::before { background: var(--warning); }
.destination-card.state-error::before { background: var(--danger); }

.section-stack { display: grid; gap: 1rem; }
.panel { position: relative; padding: 1.15rem; }
.section-heading { justify-content: space-between; align-items: flex-start; gap: 1rem; margin-bottom: .9rem; }
.section-heading h2 { color: var(--structural-cyan); }
.section-heading p { max-width: 72ch; margin: .3rem 0 0; color: var(--muted); }
.detail-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .75rem 1.25rem; margin: 0; }
.detail-item { min-width: 0; padding-bottom: .65rem; border-bottom: 1px solid var(--border-soft); }
.detail-item dt { margin-bottom: .28rem; color: var(--muted); font-size: .76rem; font-weight: 700; letter-spacing: .05em; text-transform: uppercase; }
.detail-item dd { margin: 0; overflow-wrap: anywhere; color: var(--text); }
.detail-list { margin: .55rem 0 0; padding-left: 1.25rem; }
.detail-list li + li { margin-top: .38rem; }
.detail-prose { max-width: 82ch; margin: 0; line-height: 1.65; overflow-wrap: anywhere; }
.panel h3 { margin-top: 1rem; margin-bottom: .4rem; color: var(--heading); }
.notice { margin-bottom: 1rem; padding: .8rem 1rem; border: 1px solid rgb(243 201 107 / 55%); border-radius: .7rem; background: rgb(46 37 20 / 92%); color: #ffe5a8; }
.notice.error { border-color: rgb(255 133 133 / 55%); background: #351922; color: #ffd4dc; }
.empty { padding: 1.2rem; color: var(--muted); }

.table-controls { display: flex; flex-wrap: wrap; align-items: end; gap: .65rem; margin-bottom: .8rem; }
.table-controls label { display: grid; gap: .28rem; min-width: min(100%, 13rem); color: var(--muted); font-size: .82rem; }
.table-controls input, .table-controls select { min-height: 2.75rem; padding: .5rem .65rem; border: 1px solid var(--border); border-radius: .7rem; background: var(--page); color: var(--text); font: inherit; }
.table-controls .table-search { flex: 1 1 18rem; }
.table-controls .table-filter { flex: 0 1 14rem; }
.secondary-action, .button-link { display: inline-flex; align-items: center; justify-content: center; min-height: 2.75rem; padding: .5rem .8rem; border: 1px solid var(--border); border-radius: .7rem; background: var(--surface-raised); color: var(--text); font: inherit; text-decoration: none; }
.secondary-action:hover, .button-link:hover { background: var(--surface-soft); }
.result-context { margin: 0; padding: .65rem .8rem; border-bottom: 1px solid var(--border-soft); color: var(--muted); font-size: .82rem; }
.sort-link { display: inline-flex; align-items: center; gap: .35rem; color: inherit; text-decoration: none; }
.sort-link:hover { color: var(--heading); }
.table-pagination { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: .65rem; padding: .75rem .8rem; border-top: 1px solid var(--border-soft); color: var(--muted); }
.table-pagination span { margin-right: auto; }
.data-region { overflow: hidden; border: 1px solid var(--border-soft); border-radius: .8rem; background: #070b17; }
.table-wrap { max-width: 100%; overflow-x: auto; }
table { width: 100%; min-width: 52rem; border-collapse: collapse; }
th, td { padding: .78rem .9rem; border-bottom: 1px solid var(--border-soft); text-align: left; vertical-align: top; font-size: .88rem; }
tbody tr:last-child td { border-bottom: 0; }
th { color: var(--structural-cyan); font-size: .76rem; font-weight: 750; letter-spacing: .06em; text-transform: uppercase; background: #090e1d; }
td { color: var(--text); }
.tag { display: inline-flex; align-items: center; min-height: 1.65rem; margin: .08rem .25rem .08rem 0; padding: .12rem .48rem; border: 1px solid var(--border); border-radius: 999px; color: var(--muted); font-size: .77rem; white-space: nowrap; }
.status-badge { display: inline-flex; align-items: center; min-height: 1.7rem; padding: .12rem .5rem; border: 1px solid currentColor; border-radius: 999px; font-size: .77rem; font-weight: 650; white-space: nowrap; }
.status-badge.neutral { color: var(--muted); }
.status-badge.success { color: var(--success); }
.status-badge.warning { color: var(--warning); }
.status-badge.error { color: var(--danger); }
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
code { font-size: .86em; }

.docs-layout { display: grid; grid-template-columns: 15.5rem minmax(0, 1fr) 14rem; gap: 1rem; align-items: start; }
.docs-navigation, .docs-toc { position: sticky; top: 5.65rem; padding: .9rem; }
.docs-navigation-mobile { display: none; }
.docs-navigation-mobile summary { cursor: pointer; color: var(--heading); font-weight: 700; }
.docs-navigation-mobile[open] summary { margin-bottom: .8rem; padding-bottom: .7rem; border-bottom: 1px solid var(--border-soft); }
.docs-nav-title { margin: 0 0 .55rem; color: var(--structural-pink); font-size: .76rem; font-weight: 750; letter-spacing: .07em; text-transform: uppercase; }
.docs-nav-title:not(:first-child) { margin-top: 1rem; }
.docs-nav-links { display: grid; gap: .18rem; }
.docs-nav-links a, .docs-toc a { display: block; padding: .42rem .52rem; border: 1px solid transparent; border-radius: .55rem; color: var(--muted); text-decoration: none; font-size: .86rem; }
.docs-nav-links a:hover, .docs-toc a:hover { background: var(--surface-soft); color: var(--text); }
.docs-nav-links a[aria-current="page"] { border-color: var(--active-border); background: var(--active-surface); color: var(--heading); }
.docs-nav-group { margin: .6rem .5rem .25rem; color: var(--structural-cyan); font-size: .72rem; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; }
.docs-article { padding: clamp(1rem, 2.5vw, 1.6rem); overflow-wrap: anywhere; }
.docs-article > :first-child { margin-top: 0; }
.docs-article h2 { scroll-margin-top: 6rem; margin: 2rem 0 .75rem; color: var(--structural-cyan); }
.docs-article h3 { scroll-margin-top: 6rem; margin: 1.5rem 0 .6rem; color: var(--structural-pink); }
.docs-article h4 { margin: 1.2rem 0 .5rem; }
.docs-article p, .docs-article ul, .docs-article ol { max-width: 78ch; }
.docs-article p { margin: .7rem 0; }
.docs-article li + li { margin-top: .25rem; }
.docs-article a { color: var(--accent-strong); text-decoration-thickness: .08em; text-underline-offset: .18em; }
.docs-article pre { max-width: 100%; overflow-x: auto; padding: .9rem 1rem; border: 1px solid var(--border-soft); border-radius: .7rem; background: #040713; color: var(--text); }
.docs-article :not(pre) > code { padding: .1rem .3rem; border: 1px solid var(--border-soft); border-radius: .35rem; background: #070b17; }
.docs-article blockquote { margin: 1rem 0; padding: .15rem 1rem; border-left: 3px solid var(--structural-cyan); color: var(--muted); }
.docs-article table { display: block; max-width: 100%; min-width: 0; overflow-x: auto; border: 1px solid var(--border-soft); border-radius: .7rem; }
.docs-toc h2 { margin: 0 0 .55rem; color: var(--structural-cyan); font-size: .88rem; }
.docs-toc nav { display: grid; gap: .08rem; }
.docs-toc a.level-3 { padding-left: 1.1rem; font-size: .8rem; }
.docs-toc .empty-toc { margin: 0; color: var(--muted); font-size: .82rem; }

.mobile-navigation-sheet { width: min(24rem, calc(100% - 1rem)); height: 100dvh; max-height: 100dvh; margin: 0 auto 0 0; padding: 0; border: 1px solid var(--border); border-radius: 0 1.2rem 1.2rem 0; background: var(--surface); color: var(--text); }
.mobile-navigation-sheet::backdrop { background: rgb(0 0 0 / 68%); }
.mobile-sheet-layout { min-height: 100%; padding: 1rem; }
.mobile-sheet-heading { justify-content: space-between; gap: 1rem; }
.mobile-primary-navigation { display: grid; gap: .3rem; margin-top: 1rem; }
.mobile-primary-navigation a { display: flex; align-items: center; min-height: 2.75rem; padding: .65rem .8rem; border: 1px solid transparent; border-radius: .7rem; color: var(--text); text-decoration: none; }
.mobile-primary-navigation a:hover { border-color: var(--border); background: var(--surface-raised); }
.mobile-primary-navigation a[aria-current="page"] { border-color: var(--active-border); background: var(--active-surface); }
.mobile-utility-navigation { display: grid; gap: .5rem; margin-top: 1rem; padding-top: .9rem; border-top: 1px solid var(--border); }
.mobile-utility-link { display: flex; align-items: center; gap: .7rem; min-height: 2.75rem; padding: .6rem .75rem; border: 1px solid transparent; border-radius: .7rem; color: var(--text); text-decoration: none; }
.mobile-utility-link:hover { border-color: var(--border); background: var(--surface-raised); }

@media (max-width: 1180px) {
  .site-header { grid-template-columns: minmax(12rem, 1fr) auto; }
  .primary-navigation { display: none; }
  .icon-button.mobile-menu-toggle { display: grid; }
  .header-actions { justify-self: end; }
}
@media (max-width: 1040px) {
  .docs-layout { grid-template-columns: 14rem minmax(0, 1fr); }
  .docs-toc { display: none; }
}
@media (max-width: 820px) {
  .overview-grid { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 760px) {
  .detail-grid { grid-template-columns: 1fr; }
  .site-header { grid-template-columns: minmax(0, 1fr) auto; gap: .55rem; padding-inline: .65rem; }
  .header-actions .mode-badge, .header-actions .availability-badge, .header-actions .utility-help { display: none; }
  .site-header .brand-copy small { display: block; }
  main { width: min(calc(100% - 1rem), 92rem); }
  .page-heading-row { margin-bottom: 1rem; }
  .overview-grid, .docs-layout { grid-template-columns: 1fr; }
  .destination-card { min-height: 0; }
  .docs-navigation-desktop { display: none; }
  .docs-navigation-mobile { display: block; position: static; }
  .docs-article { grid-row: 2; }
  .table-controls { align-items: stretch; }
  .table-controls > * { width: 100%; }
  .table-wrap { overflow: visible; overscroll-behavior-inline: contain; }
  .data-table { min-width: 0; }
  .data-table, .data-table tbody, .data-table tr, .data-table td { display: block; width: 100%; }
  .data-table thead { position: absolute; width: 1px; height: 1px; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; }
  .data-table tbody { display: grid; gap: .65rem; padding: .65rem; }
  .data-table tr { padding: .7rem; border: 1px solid var(--border-soft); border-radius: .7rem; background: var(--surface-raised); }
  .data-table td { display: grid; grid-template-columns: minmax(7rem, .35fr) minmax(0, 1fr); gap: .6rem; padding: .42rem 0; border: 0; overflow-wrap: anywhere; }
  .data-table td::before { content: attr(data-label); color: var(--muted); font-size: .78rem; font-weight: 650; }
  .table-pagination { justify-content: stretch; }
  .table-pagination span { width: 100%; margin-right: 0; }
  .table-pagination .button-link { flex: 1; }
}
@media (max-width: 460px) {
  .brand-copy strong { font-size: .94rem; }
  .brand-copy small { font-size: .66rem; }
  .brand-mark { width: 2.35rem; height: 2.35rem; }
  .overview-grid { grid-template-columns: 1fr; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior: auto !important; transition-duration: .01ms !important; animation-duration: .01ms !important; animation-iteration-count: 1 !important; }
  html.is-navigating .navigation-progress::after { left: 0; width: 100%; transform: none; animation: none; }
}
@media (forced-colors: active) {
  .destination-card::before, .panel-accent::before { background: CanvasText; }
  .primary-navigation a[aria-current="page"], .mobile-primary-navigation a[aria-current="page"], .docs-nav-links a[aria-current="page"] { border: 2px solid Highlight; background: Canvas; color: CanvasText; }
}
"""

JAVASCRIPT = r"""
(() => {
  const startNavigationProgress = () => {
    document.documentElement.classList.add("is-navigating");
    document.body.setAttribute("aria-busy", "true");
  };
  const stopNavigationProgress = () => {
    document.documentElement.classList.remove("is-navigating");
    document.body.removeAttribute("aria-busy");
  };

  window.addEventListener("pageshow", stopNavigationProgress);
  window.addEventListener("beforeunload", startNavigationProgress);
  document.addEventListener("click", (event) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const target = event.target;
    if (!(target instanceof Element)) return;
    const link = target.closest("a[href]");
    if (!link || link.hasAttribute("download") || (link.target && link.target !== "_self")) return;
    const destination = new URL(link.href, window.location.href);
    if (destination.origin !== window.location.origin) return;
    if (
      destination.pathname === window.location.pathname &&
      destination.search === window.location.search &&
      destination.hash === window.location.hash
    ) return;
    if (
      destination.pathname === window.location.pathname &&
      destination.search === window.location.search &&
      destination.hash
    ) return;
    startNavigationProgress();
  });
  const toggle = document.getElementById("mobile-menu-toggle");
  const dialog = document.getElementById("mobile-navigation");
  const closeButton = document.getElementById("close-mobile-navigation");
  if (!toggle || !dialog || !closeButton) return;

  let restoreFocus = null;

  const setExpanded = (expanded) => toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
  const closeNavigation = () => {
    if (dialog.open) dialog.close();
  };

  toggle.addEventListener("click", () => {
    restoreFocus = document.activeElement;
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
    setExpanded(true);
    const current = dialog.querySelector('[aria-current="page"]');
    (current || closeButton).focus();
  });

  closeButton.addEventListener("click", closeNavigation);
  dialog.querySelectorAll("a").forEach((link) => link.addEventListener("click", closeNavigation));

  dialog.addEventListener("close", () => {
    setExpanded(false);
    if (restoreFocus instanceof HTMLElement) restoreFocus.focus();
  });

  dialog.addEventListener("cancel", () => setExpanded(false));

  document.querySelectorAll(".table-controls").forEach((form) => {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const params = new URLSearchParams();
      for (const [key, value] of new FormData(form).entries()) {
        const text = String(value).trim();
        if (text) params.set(key, text);
      }
      const query = params.toString();
      startNavigationProgress();
      window.location.assign(`${window.location.pathname}${query ? `?${query}` : ""}`);
    });
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && dialog.open) closeNavigation();
  });
})();
"""
