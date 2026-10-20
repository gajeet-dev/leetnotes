// Keeps the Basecoat sidebar in step with the window width.
// Basecoat decides open/closed once at page load, so crossing the
// breakpoint would otherwise leave the sidebar in the wrong state.
(() => {
  const BREAKPOINT_PX = 768; // Basecoat's default, and Tailwind's "md"
  const desktop = window.matchMedia(`(min-width: ${BREAKPOINT_PX}px)`);

  const syncSidebar = (isDesktop) => {
    const sidebar = document.getElementById("sidebar");
    if (!sidebar) return;
    if (isDesktop) sidebar.open?.();
    else sidebar.close?.();
  };

  desktop.addEventListener("change", (event) => syncSidebar(event.matches));
})();
