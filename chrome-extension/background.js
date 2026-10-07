/**
 * background.js — GCC Job Radar Service Worker
 * Manages Side Panel behavior, context menus, and background events.
 */

// Configure Chrome to open Side Panel when action toolbar button is clicked
if (chrome.sidePanel && chrome.sidePanel.setPanelBehavior) {
  chrome.sidePanel
    .setPanelBehavior({ openPanelOnActionClick: true })
    .catch((error) => console.warn("Side panel behavior warning:", error));
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "gcc_evaluate_selection",
    title: "Evaluate & Tailor Resume for Selection (Open Radar)",
    contexts: ["selection"],
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId === "gcc_evaluate_selection" && tab && tab.id) {
    await chrome.storage.local.set({
      selected_jd_text: info.selectionText,
      page_url: tab.url,
      page_title: tab.title,
    });

    // Open Side Panel programmatically on right-click selection
    if (chrome.sidePanel && chrome.sidePanel.open && tab.windowId) {
      try {
        await chrome.sidePanel.open({ windowId: tab.windowId });
      } catch (err) {
        console.warn("Could not open side panel programmatically:", err);
      }
    }

    chrome.action.setBadgeText({ tabId: tab.id, text: "JD" });
    chrome.action.setBadgeBackgroundColor({ tabId: tab.id, color: "#0ea5e9" });
  }
});

// Fallback for Chromium builds where openPanelOnActionClick isn't active
chrome.action.onClicked.addListener(async (tab) => {
  if (chrome.sidePanel && chrome.sidePanel.open && tab && tab.windowId) {
    try {
      await chrome.sidePanel.open({ windowId: tab.windowId });
    } catch (err) {
      console.warn("Sidepanel open fallback error:", err);
    }
  }
});
