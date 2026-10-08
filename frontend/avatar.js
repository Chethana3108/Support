/**
 * avatar.js — Static Clean Photo Avatar Controller
 * 
 * Displays the official avatar photograph cleanly without any
 * overlays, status text, thinking/online indicators, or distortions.
 */

let avatarWrap = null;
let avatarPoster = null;
let currentState = "idle";

/**
 * Initialize avatar and remove any legacy overlays if present.
 */
export function initAvatar() {
  avatarWrap = document.getElementById("bz-avatar");
  avatarPoster = document.getElementById("bz-avatar-poster");

  // Clean up any dynamic canvas/video/morph overlays if still in DOM
  if (avatarWrap) {
    const oldCanvas = avatarWrap.querySelector(".bz-avatar-lip-canvas, .bz-lip-canvas");
    if (oldCanvas) oldCanvas.remove();

    const oldMorph = avatarWrap.querySelector(".bz-mouth-morph-container");
    if (oldMorph) oldMorph.remove();

    const oldVid = avatarWrap.querySelector("video");
    if (oldVid) oldVid.remove();

    const oldBadge = avatarWrap.querySelector(".bz-status-badge");
    if (oldBadge) oldBadge.remove();

    const oldSpeak = avatarWrap.querySelector(".bz-speak-pill");
    if (oldSpeak) oldSpeak.remove();
  }

  setAvatarState("idle");
}

/**
 * Avatar state setter (pure static mode, no indicators or animations).
 */
export function setAvatarState(state) {
  currentState = state || "idle";
  if (!avatarWrap) return;
  // Ensure no animating classes on the avatar wrap
  avatarWrap.classList.remove("speaking", "listening", "thinking");
}

/**
 * Word event hook (preserved for API compatibility).
 */
export function onWordSpoken(word) {
  // Pure static photo mode
}

/**
 * Current avatar state getter.
 */
export function getAvatarState() {
  return currentState;
}
