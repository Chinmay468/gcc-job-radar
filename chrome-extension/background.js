/**
 * background.js — GCC Job Radar Service Worker
 * Manages context menu and extension messaging.
 */

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "gcc_evaluate_selection",
    title: "Evaluate & Tailor Resume for Selection",
    contexts: ["selection"],
  });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === "gcc_evaluate_selection" && tab.id) {
    chrome.storage.local.set({
      selected_jd_text: info.selectionText,
      page_url: tab.url,
      page_title: tab.title,
    });
    // Open action popup if supported or highlight badge
    chrome.action.setBadgeText({ tabId: tab.id, text: "JD" });
    chrome.action.setBadgeBackgroundColor({ tabId: tab.id, color: "#0ea5e9" });
  }
});
