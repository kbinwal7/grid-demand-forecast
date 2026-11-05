export function initPanelControls() {
  for (const button of document.querySelectorAll("[data-panel-expand]")) {
    button.addEventListener("click", async () => {
      const panel = button.closest("[data-panel]");
      try {
        if (document.fullscreenElement === panel) {
          await document.exitFullscreen();
        } else {
          await panel.requestFullscreen();
        }
      } catch {
        const notice = document.querySelector("#dashboard-notice");
        notice.textContent = "Fullscreen is not available in this browser.";
        notice.hidden = false;
      }
    });
  }
}

export function applyTileStatus(tile, status) {
  tile.classList.remove("status-green", "status-yellow", "status-red");
  if (status !== "neutral") {
    tile.classList.add(`status-${status}`);
  }
}
