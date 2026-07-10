// Base-path support for serving Gater under a URL prefix (e.g. /user/<name>/
// behind an Ingress). window.GATER_BASE_PATH is injected by the template
// (empty string when served at the root, so this is a no-op in that case).
//
// Root-absolute request URLs ("/get_channel_names", "/generated/.../0_0.png")
// are rewritten to include the prefix. Static <script>/<link>/<img> tags are
// handled in the templates via {{ base_path }} instead — this file only covers
// URLs built at runtime by JS (fetch, XHR, d3, OpenSeadragon tile AJAX).
(function () {
    var BASE = (window.GATER_BASE_PATH || "").replace(/\/+$/, "");
    window.GATER_BASE_PATH = BASE;

    // Prepend BASE to a root-absolute URL, idempotently. Leaves relative URLs,
    // full URLs (http:, data:, blob:), protocol-relative (//host) and
    // already-prefixed URLs untouched.
    function withBase(url) {
        if (!BASE || typeof url !== "string" || url.length === 0) return url;
        if (url.charAt(0) !== "/") return url;                 // relative / scheme
        if (url.charAt(1) === "/") return url;                 // protocol-relative
        if (url === BASE || url.slice(0, BASE.length + 1) === BASE + "/") return url;
        return BASE + url;
    }

    // Exposed so app code can build prefixed URLs explicitly if ever needed.
    window.gaterUrl = withBase;

    // fetch (also covers d3-fetch / d3.json, which delegate to window.fetch).
    if (window.fetch) {
        var _fetch = window.fetch.bind(window);
        window.fetch = function (input, init) {
            if (typeof input === "string") {
                return _fetch(withBase(input), init);
            }
            if (input && typeof input.url === "string") {
                return _fetch(new Request(withBase(input.url), input), init);
            }
            return _fetch(input, init);
        };
    }

    // XMLHttpRequest (covers jQuery $.ajax/.load and OpenSeadragon's
    // loadTilesWithAjax tile requests).
    if (window.XMLHttpRequest) {
        var _open = XMLHttpRequest.prototype.open;
        XMLHttpRequest.prototype.open = function (method, url) {
            arguments[1] = withBase(url);
            return _open.apply(this, arguments);
        };
    }
})();
