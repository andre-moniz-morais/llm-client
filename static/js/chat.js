/* The chat page.

   Sending a message posts the composer and appends the HTML the backend
   rendered for the pair of messages, so the transcript markup only exists in
   the Django template. The reply is produced by the background worker, so it
   usually arrives pending and is re-fetched until it resolves. */

(function () {
  "use strict";

  const { $, $$, postForm, getFragment, elementFrom, errorFragment, busy, initAttachments, notify } =
    window.CRAFT;

  // Quick at first, since short replies land in a couple of seconds, then
  // easing off for the long ones.
  const POLL_STEPS = [800, 1200, 1500, 2000, 3000];

  document.addEventListener("DOMContentLoaded", () => {
    const form = $("[data-chat-form]");
    const transcript = $("[data-transcript]");
    if (!form || !transcript) return;

    const textarea = form.querySelector("textarea[name='message']");
    const submit = form.querySelector("[data-send]");
    const conversationInput = $("[data-conversation-input]", form);
    const modelInput = $("[data-model-input]", form);
    const modelSelect = $("[data-model-select]");
    const attachments = initAttachments(form);
    const notifyOnReply = form.dataset.notify === "true";

    /* Keep the hidden model field in step with the picker. Changing model
       mid-thread continues the same conversation with the new model. */
    if (modelSelect && modelInput) {
      modelSelect.addEventListener("change", () => {
        modelInput.value = modelSelect.value;
      });
    }

    const scrollToEnd = () => {
      transcript.scrollTop = transcript.scrollHeight;
    };

    /** Re-fetch a pending reply until it resolves; returns the final node. */
    function waitForReply(node) {
      return new Promise((resolve) => {
        let attempt = 0;
        const tick = async () => {
          const delay = POLL_STEPS[Math.min(attempt, POLL_STEPS.length - 1)];
          attempt += 1;
          try {
            const result = await getFragment(node.dataset.pollUrl);
            const fresh = result.ok ? elementFrom(result.html).firstElementChild : null;
            if (fresh) {
              const atEnd =
                transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 40;
              node.replaceWith(fresh);
              node = fresh;
              if (atEnd) scrollToEnd();
              if (!fresh.dataset.pollUrl) return resolve(fresh);
            }
          } catch (error) {
            // A dropped poll is not fatal - the reply is safe on the server.
          }
          setTimeout(tick, delay);
        };
        setTimeout(tick, POLL_STEPS[0]);
      });
    }

    function placeholder() {
      const article = document.createElement("article");
      article.className = "msg msg-assistant";
      article.dataset.pending = "true";
      article.innerHTML =
        '<div class="msg-avatar" aria-hidden="true">AI</div>' +
        '<div class="msg-body"><div class="msg-content">' +
        '<span class="typing"><span></span><span></span><span></span></span>' +
        "</div></div>";
      return article;
    }

    async function send() {
      const text = textarea.value.trim();
      const hasFiles = attachments && form.querySelector("[data-attach-input]").files.length;
      if (!text && !hasFiles) return;

      const emptyState = transcript.querySelector("[data-empty-state]");
      if (emptyState) emptyState.remove();

      const formData = new FormData(form);
      const pending = placeholder();
      transcript.appendChild(pending);
      scrollToEnd();

      textarea.value = "";
      textarea.style.height = "auto";
      if (attachments) attachments.clear();
      busy(submit, true, "Sending");

      try {
        const result = await postForm(form.action, formData);
        pending.remove();

        if (result.ok) {
          const fragment = elementFrom(result.html);
          // Read the reply out of the fragment before appending it, which
          // empties it, so the notification can quote the answer.
          let reply = fragment.querySelector(".msg-assistant");
          transcript.appendChild(fragment);

          // A brand-new conversation gets its id back in a header, so the next
          // turn continues the same thread instead of starting another one.
          const id = result.headers.get("X-Conversation-Id");
          if (id && conversationInput && !conversationInput.value) {
            conversationInput.value = id;
            const url = new URL(window.location.href);
            url.searchParams.set("conversation", id);
            window.history.replaceState({}, "", url);
          }

          // Keep the composer busy until the reply is in, so the next turn
          // is sent with this one in its history.
          if (reply && reply.dataset.pollUrl) {
            scrollToEnd();
            reply = await waitForReply(reply);
          }

          // After the id is known, so the tag identifies the right thread.
          if (notifyOnReply) notifyReply(reply);
        } else {
          transcript.appendChild(
            result.html ? elementFrom(result.html) : errorFragment("The request failed.")
          );
        }
      } catch (error) {
        pending.remove();
        transcript.appendChild(
          errorFragment("Could not reach the server. Check your connection and try again.")
        );
      } finally {
        busy(submit, false);
        scrollToEnd();
        textarea.focus();
      }
    }

    /** Tell the user a reply arrived, if they are looking elsewhere. */
    function notifyReply(node) {
      if (!node) return;
      const model = node.querySelector(".msg-meta strong");
      const content = node.querySelector(".msg-content");
      notify(
        model ? model.textContent.trim() : "New reply",
        content ? content.textContent.trim().replace(/\s+/g, " ") : "",
        // One tag per conversation, so a second reply replaces the first
        // instead of stacking up while the tab is in the background.
        `craft-chat-${conversationInput ? conversationInput.value : ""}`
      );
    }

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      send();
    });

    textarea.addEventListener("keydown", (event) => {
      // Enter sends; Shift+Enter inserts a newline. On touch keyboards Enter
      // should insert a newline instead, since there is no visible Send key.
      const isTouch = window.matchMedia("(pointer: coarse)").matches;
      if (event.key === "Enter" && !event.shiftKey && !isTouch) {
        event.preventDefault();
        send();
      }
    });

    // A reply still being written when the page was (re)loaded.
    $$("[data-poll-url]", transcript).forEach((node) => {
      waitForReply(node).then((reply) => {
        if (notifyOnReply) notifyReply(reply);
      });
    });

    scrollToEnd();
    textarea.focus();
  });
})();
