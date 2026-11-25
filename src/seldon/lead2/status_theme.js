const themeRoot = document.documentElement;
let selectedTheme = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
try {
  const savedTheme = localStorage.getItem("seldon-theme");
  if (savedTheme === "light" || savedTheme === "dark") selectedTheme = savedTheme;
} catch {}
themeRoot.dataset.theme = selectedTheme;

document.addEventListener("DOMContentLoaded", () => {
  const button = document.getElementById("theme");
  function updateButton() {
    button.textContent = selectedTheme === "dark" ? "Light" : "Dark";
    button.setAttribute("aria-label", "Switch to " + button.textContent.toLowerCase() + " theme");
  }
  updateButton();
  button.addEventListener("click", () => {
    selectedTheme = selectedTheme === "dark" ? "light" : "dark";
    themeRoot.dataset.theme = selectedTheme;
    try { localStorage.setItem("seldon-theme", selectedTheme); } catch {}
    updateButton();
  });
});
