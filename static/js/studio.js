/* The image / video / music pages.

   Three server round trips make up the flow, each returning rendered HTML:
   picking a model fetches its form, submitting starts a task and returns its
   card, and the card is re-fetched until the task is reported finished. */

(function () {
  "use strict";

  const {
    $,
    $$,
    postForm,
    getFragment,
    setHTML,
    elementFrom,
    errorFragment,
    busy,
    initAttachments,
    initAutogrow,
    initRanges,
  } = window.CRAFT;

  // Poll gently at first, then back off, so a slow video render does not mean
  // hundreds of requests.
  const POLL_STEPS = [2000, 3000, 5000, 8000];
  const POLL_MAX = 15000;

  document.addEventListener("DOMContentLoaded", () => {
    const studio = $("[data-studio]");
    if (!studio) return;

    const formHost = $("[data-model-form-host]");
    const results = $("[data-results]");
    const modelSelect = $("[data-model-select]");

    // The templates carry placeholder URLs reversed by Django, so the routes
    // stay defined in urls.py rather than being rebuilt here.
    const formUrl = (slug) => studio.dataset.formUrl.replace("SLUG", encodeURIComponent(slug));
    const statusUrl = (id) => studio.dataset.statusUrl.replace("/0/", `/${id}/`);

    /* -------------------------------------------------------- model form */

    // Each model's schema is fetched when it is picked, so there is a moment
    // where the visible form belongs to the previous model. `inert` takes it
    // out of play for that moment; submitting it would call the wrong endpoint.
    let loadToken = 0;

    async function loadForm(slug) {
      if (!slug || !formHost) return;

      const token = ++loadToken;
      formHost.setAttribute("aria-busy", "true");
      formHost.inert = true;
      formHost.classList.add("is-loading");

      try {
        const result = await getFragment(formUrl(slug));
        // A quick second pick can land out of order; only the latest wins.
        if (token !== loadToken) return;

        setHTML(formHost, result.html);
        bindForm();

        const url = new URL(window.location.href);
        url.searchParams.set("model", slug);
        window.history.replaceState({}, "", url);
      } catch (error) {
        if (token !== loadToken) return;
        setHTML(formHost, "");
        formHost.appendChild(errorFragment("Could not load this model's options."));
      } finally {
        if (token === loadToken) {
          formHost.removeAttribute("aria-busy");
          formHost.inert = false;
          formHost.classList.remove("is-loading");
        }
      }
    }

    if (modelSelect) {
      modelSelect.addEventListener("change", () => loadForm(modelSelect.value));
    }

    /* -------------------------------------------------------- submitting */

    function bindForm() {
      const form = $("[data-generate-form]", formHost);
      if (!form) return;

      initAutogrow(form);
      initRanges(form);
      const attachments = initAttachments(form);

      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const submit = $("[data-generate-submit]", form);
        busy(submit, true, "Starting…");

        try {
          const result = await postForm(form.action, new FormData(form));
          const fragment = result.html
            ? elementFrom(result.html)
            : errorFragment("The request failed.");

          const empty = $("[data-results-empty]", results);
          if (empty && result.ok) empty.remove();

          const card = fragment.firstElementChild;
          results.prepend(fragment);

          if (result.ok && card && card.hasAttribute("data-poll")) {
            watch(card);
            if (attachments) attachments.clear();
          }
          if (card) card.scrollIntoView({ behavior: "smooth", block: "nearest" });
        } catch (error) {
          results.prepend(errorFragment("Could not reach the server."));
        } finally {
          busy(submit, false);
        }
      });
    }

    /* ----------------------------------------------------------- polling */

    function watch(card) {
      const id = card.dataset.generation;
      if (!id) return;

      let attempt = 0;

      const tick = async () => {
        const delay = POLL_STEPS[Math.min(attempt, POLL_STEPS.length - 1)] || POLL_MAX;
        attempt += 1;

        // Nothing useful happens while the tab is hidden; wait for it to return.
        if (document.hidden) {
          document.addEventListener("visibilitychange", tick, { once: true });
          return;
        }

        try {
          const result = await getFragment(statusUrl(id));
          if (!result.ok) return;

          const fresh = elementFrom(result.html).firstElementChild;
          const current = document.getElementById(`generation-${id}`);
          if (!fresh || !current) return;

          current.replaceWith(fresh);
          if (fresh.hasAttribute("data-poll")) {
            setTimeout(tick, delay);
          }
        } catch (error) {
          // A dropped poll is not fatal - try again on the next interval.
          setTimeout(tick, POLL_MAX);
        }
      };

      setTimeout(tick, POLL_STEPS[0]);
    }

    // Anything already running when the page loaded needs watching too.
    $$("[data-poll]", results).forEach(watch);

    bindForm();
  });
})();
