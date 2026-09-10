/* Shared behaviour: navigation, the model picker, and the small helpers the
   chat and studio pages build on.

   The backend answers with rendered HTML, so this file is deliberately thin -
   it moves fragments into the page rather than building markup of its own. */

(function () {
  "use strict";

  /* ---------------------------------------------------------------- utils */

  const $ = (selector, root) => (root || document).querySelector(selector);
  const $$ = (selector, root) =>
    Array.from((root || document).querySelectorAll(selector));

  function csrfToken() {
    const field = $('input[name="csrfmiddlewaretoken"]');
    if (field) return field.value;
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  /** POST a form and return the HTML fragment the backend rendered. */
  async function postForm(url, formData) {
    const response = await fetch(url, {
      method: "POST",
      body: formData,
      headers: { "X-CSRFToken": csrfToken(), "X-Requested-With": "fetch" },
      credentials: "same-origin",
    });
    const html = await response.text();
    return { ok: response.ok, html, headers: response.headers, status: response.status };
  }

  async function getFragment(url) {
    const response = await fetch(url, {
      headers: { "X-Requested-With": "fetch" },
      credentials: "same-origin",
    });
    return { ok: response.ok, html: await response.text(), headers: response.headers };
  }

  /** Replace an element's contents with a server-rendered fragment. */
  function setHTML(target, html) {
    if (target) target.innerHTML = html;
  }

  function elementFrom(html) {
    const template = document.createElement("template");
    template.innerHTML = html.trim();
    return template.content;
  }

  function errorFragment(message) {
    const div = document.createElement("div");
    div.className = "ai-html ai-html-error";
    const callout = document.createElement("div");
    callout.className = "callout callout-warn";
    callout.textContent = message;
    div.appendChild(callout);
    return div;
  }

  function busy(button, isBusy, busyLabel) {
    if (!button) return;
    const label = button.querySelector("span");
    if (isBusy) {
      button.classList.add("is-busy");
      button.disabled = true;
      if (label && busyLabel) {
        button.dataset.idleLabel = label.textContent;
        label.textContent = busyLabel;
      }
    } else {
      button.classList.remove("is-busy");
      button.disabled = false;
      if (label && button.dataset.idleLabel) label.textContent = button.dataset.idleLabel;
    }
  }

  /* ----------------------------------------------------------- navigation */

  function initNavigation() {
    const nav = $("[data-nav]");
    const toggle = $("[data-nav-toggle]");
    const scrim = $("[data-nav-scrim]");
    if (!nav || !toggle) return;

    const setOpen = (open) => {
      nav.classList.toggle("is-open", open);
      toggle.setAttribute("aria-expanded", String(open));
      if (scrim) scrim.hidden = !open;
    };

    toggle.addEventListener("click", () => setOpen(!nav.classList.contains("is-open")));
    if (scrim) scrim.addEventListener("click", () => setOpen(false));
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") setOpen(false);
    });
    // A tap on a destination should not leave the drawer covering the page.
    $$(".nav-item", nav).forEach((item) =>
      item.addEventListener("click", () => setOpen(false))
    );
  }

  /* --------------------------------------------------------- model picker */

  /** Wire the search box and provider dropdown to the model <select>.
   *
   * Filtering happens in the browser over options the page already has, so
   * narrowing a 180-model list costs nothing. */
  function initModelBar() {
    const bar = $("[data-model-bar]");
    if (!bar) return;

    const select = $("[data-model-select]", bar);
    const search = $("[data-model-search]", bar);
    const provider = $("[data-provider-select]", bar);
    const count = $("[data-model-count]", bar);
    if (!select) return;

    const options = $$("option", select).map((option) => ({
      option,
      group: option.parentElement,
      haystack: option.dataset.search || option.textContent.toLowerCase(),
      provider: option.dataset.provider || "",
    }));

    function apply() {
      const term = (search && search.value.trim().toLowerCase()) || "";
      const wanted = (provider && provider.value) || "";
      let visible = 0;

      options.forEach((entry) => {
        const matches =
          (!term || entry.haystack.includes(term)) &&
          (!wanted || entry.provider === wanted);
        entry.option.hidden = !matches;
        entry.option.disabled = !matches;
        if (matches) visible += 1;
      });

      // A group whose options are all hidden should not leave a stray label.
      $$("optgroup", select).forEach((group) => {
        group.hidden = !$$("option:not([hidden])", group).length;
      });

      if (count) count.textContent = `${visible} model${visible === 1 ? "" : "s"}`;

      // Keep the selection valid: jump to the first match if it was filtered out.
      const current = select.selectedOptions[0];
      if (current && current.hidden) {
        const first = options.find((entry) => !entry.option.hidden);
        if (first) {
          select.value = first.option.value;
          select.dispatchEvent(new Event("change", { bubbles: true }));
        }
      }
    }

    if (search) search.addEventListener("input", apply);
    if (provider) provider.addEventListener("change", apply);
    apply();
  }

  /* ------------------------------------------------------------- controls */

  function initAutogrow(root) {
    $$("[data-autogrow]", root || document).forEach((textarea) => {
      const grow = () => {
        textarea.style.height = "auto";
        textarea.style.height = `${Math.min(textarea.scrollHeight, 190)}px`;
      };
      textarea.addEventListener("input", grow);
      grow();
    });
  }

  function initRanges(root) {
    $$("[data-range]", root || document).forEach((input) => {
      const output = input.parentElement.querySelector("output");
      if (!output) return;
      const sync = () => {
        output.textContent = input.value;
      };
      input.addEventListener("input", sync);
      sync();
    });
  }

  /** A file input plus a thumbnail strip that can drop individual files. */
  function initAttachments(scope) {
    const input = $("[data-attach-input], [data-reference-input]", scope);
    const preview = $("[data-attach-preview]", scope);
    if (!input || !preview) return null;

    let files = [];

    function render() {
      preview.textContent = "";
      files.forEach((file, index) => {
        const thumb = document.createElement("div");
        thumb.className = "attach-thumb";

        const image = document.createElement("img");
        image.alt = file.name;
        image.src = URL.createObjectURL(file);
        image.addEventListener("load", () => URL.revokeObjectURL(image.src), { once: true });

        const remove = document.createElement("button");
        remove.type = "button";
        remove.setAttribute("aria-label", `Remove ${file.name}`);
        remove.innerHTML =
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="m6 6 12 12M18 6 6 18"/></svg>';
        remove.addEventListener("click", () => {
          files.splice(index, 1);
          sync();
        });

        thumb.append(image, remove);
        preview.appendChild(thumb);
      });
    }

    function sync() {
      // Reflect our list back onto the input so a normal form POST carries it.
      const transfer = new DataTransfer();
      files.forEach((file) => transfer.items.add(file));
      input.files = transfer.files;
      render();
    }

    function add(newFiles) {
      files = files.concat(Array.from(newFiles).filter((file) => file.type.startsWith("image/")));
      sync();
    }

    input.addEventListener("change", () => {
      files = Array.from(input.files);
      render();
    });

    const trigger = $("[data-attach-trigger]", scope);
    if (trigger) trigger.addEventListener("click", () => input.click());

    const dropzone = $("[data-dropzone]", scope);
    if (dropzone) {
      dropzone.addEventListener("click", () => input.click());
      dropzone.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          input.click();
        }
      });
      ["dragenter", "dragover"].forEach((name) =>
        dropzone.addEventListener(name, (event) => {
          event.preventDefault();
          dropzone.classList.add("is-over");
        })
      );
      ["dragleave", "drop"].forEach((name) =>
        dropzone.addEventListener(name, () => dropzone.classList.remove("is-over"))
      );
      dropzone.addEventListener("drop", (event) => {
        event.preventDefault();
        if (event.dataTransfer && event.dataTransfer.files.length) add(event.dataTransfer.files);
      });
    }

    return {
      clear() {
        files = [];
        sync();
      },
    };
  }

  /* -------------------------------------------------------- notifications */

  /* Desktop notifications for replies that land while the user is looking at
     something else. The account preference gates this on the server; here it is
     gated on the browser's own permission grant, which is per browser and per
     device and can only be requested from a real user gesture. */

  function notificationsSupported() {
    return (
      "Notification" in window && typeof Notification.requestPermission === "function"
    );
  }

  function notificationPermission() {
    return notificationsSupported() ? Notification.permission : "unsupported";
  }

  /** The installed app's icon, read from the page so a hashed static URL works. */
  function appIcon() {
    const link = $('link[rel="apple-touch-icon"]');
    return link ? link.href : undefined;
  }

  /** Raise a notification, unless the user is already looking at the page.
   *
   * `hasFocus` covers the case the visibility API does not: a visible tab in a
   * browser window sitting behind another application. */
  function notify(title, body, tag) {
    if (notificationPermission() !== "granted") return null;
    if (document.visibilityState === "visible" && document.hasFocus()) return null;

    try {
      const notification = new Notification(title, {
        body: (body || "").slice(0, 180),
        icon: appIcon(),
        tag: tag || "craft",
      });
      notification.addEventListener("click", () => {
        window.focus();
        notification.close();
      });
      return notification;
    } catch (error) {
      /* Some browsers only allow the constructor from a service worker. There
         is nothing to fall back to, and a missed notification is not an error. */
      return null;
    }
  }

  /** The permission read-out and the request button on the settings page. */
  function initNotificationSettings() {
    const panel = $("[data-browser-notifications]");
    if (!panel) return;

    const state = $("[data-notify-state]", panel);
    const request = $("[data-notify-request]", panel);
    if (!state || !request) return;

    // Static markup of our own, so innerHTML carries no untrusted content.
    const READOUTS = {
      unsupported:
        '<span class="pill pill-warn">Unsupported</span> This browser cannot show notifications.',
      granted: '<span class="pill pill-ok">Allowed</span> This browser will show notifications.',
      denied:
        '<span class="pill pill-danger">Blocked</span> Notifications are blocked for this site in your browser settings.',
      default:
        '<span class="pill pill-warn">Not allowed yet</span> Your browser has not been asked yet.',
    };

    function paint() {
      const permission = notificationPermission();
      state.innerHTML = READOUTS[permission] || READOUTS.default;
      request.hidden = permission !== "default";
    }

    request.addEventListener("click", () => {
      Notification.requestPermission().then(paint).catch(paint);
    });

    paint();
  }

  /* ------------------------------------------------------------------ pwa */

  function initServiceWorker() {
    if (!("serviceWorker" in navigator)) return;
    if (location.protocol !== "https:" && location.hostname !== "localhost") return;
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/service-worker.js").catch(() => {
        /* Offline support is a bonus; a failed registration changes nothing. */
      });
    });
  }

  /* ---------------------------------------------------------------- boot */

  window.CRAFT = {
    $,
    $$,
    postForm,
    getFragment,
    setHTML,
    elementFrom,
    errorFragment,
    busy,
    initAutogrow,
    initRanges,
    initAttachments,
    notify,
    notificationPermission,
  };

  document.addEventListener("DOMContentLoaded", () => {
    initNavigation();
    initModelBar();
    initAutogrow();
    initRanges();
    initNotificationSettings();
    initServiceWorker();
  });
})();
