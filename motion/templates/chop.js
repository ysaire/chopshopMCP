window.chop = function () {
  const root = document.querySelector("[data-composition-id]");
  const W = Number(root.dataset.width) || 1920;
  const H = Number(root.dataset.height) || 1080;
  const D = Number(root.dataset.duration) || 4;
  const vars = window.__hyperframes && window.__hyperframes.getVariables ? window.__hyperframes.getVariables() : {};
  const vertical = H > W;
  const css = document.documentElement.style;
  css.setProperty("--u", Math.min(W, H) / 1080 + "px");
  css.setProperty("--sx", Math.round(W * (vertical ? 0.07 : 0.06)) + "px");
  css.setProperty("--sy", Math.round(H * 0.08) + "px");
  // a vertical frame's bottom margin clears youtube's shorts interface
  css.setProperty("--sb", Math.round(H * (vertical ? 0.24 : 0.12)) + "px");
  if (vars.accent) css.setProperty("--accent", vars.accent);
  const text = (id, value) => {
    const el = document.getElementById(id);
    el.textContent = value == null ? "" : String(value);
    return el;
  };
  const list = (value) => String(value || "").split("|").map((x) => x.trim()).filter(Boolean);
  const number = (value, decimals, locale) =>
    new Intl.NumberFormat(locale || "en-US", { minimumFractionDigits: decimals || 0, maximumFractionDigits: decimals || 0 }).format(value);
  return { root, W, H, D, vars, vertical, text, list, number };
};
