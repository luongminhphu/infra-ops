// Applies the stored colour-scheme override before first paint (no flash).
// Kept as a tiny external file so a strict Content-Security-Policy needs no 'unsafe-inline'.
try {
  var stored = localStorage.getItem("color-scheme");
  if (stored === "light" || stored === "dark") {
    document.querySelector('meta[name="color-scheme"]').content = stored;
  }
} catch (e) {
  /* storage blocked: follow the system preference */
}
