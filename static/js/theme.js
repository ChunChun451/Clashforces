// Runs before paint to avoid a theme flash. Dark is the default.
(function () {
  try {
    var t = localStorage.getItem("theme");
    if (t === "light") document.documentElement.dataset.theme = "light";
  } catch (e) { /* storage blocked */ }
})();
