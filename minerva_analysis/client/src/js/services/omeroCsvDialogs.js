// Shared UI helpers for the OMERO CSV load/save flows used by BOTH the
// Channels panel and the CSV Gating panel.
//
// Kept in one place because the two panels must agree on how a CSV is named:
// the same string is the OMERO filename, the annotation description and the
// collision key, so any divergence between them would let two files that look
// identical in OMERO.web coexist without ever warning about a clash.
(function () {

    // Mirrors data_model.canonical_csv_name. Substitution is one character for
    // one, so a caller echoing this back into a text input never has to move
    // the caret.
    function sanitize(v) {
        return (v || '').replace(/[^A-Za-z0-9._-]/g, '_');
    }

    function canonical(v) {
        var n = sanitize((v || '').trim());
        if (n && !/\.csv$/i.test(n)) n += '.csv';
        return n;
    }

    /**
     * Substitute disallowed characters in a text input AS THE USER TYPES, so
     * the box always shows the name that will actually be written. Because the
     * substitution is one character for one, the caret can simply be put back
     * where it was. Safe to call on an unfocused input (e.g. to clean a default
     * value on load), where there is no caret to restore.
     * @returns {String} the cleaned value
     */
    function sanitizeInputInPlace(input) {
        var clean = sanitize(input.value);
        if (clean !== input.value) {
            var caret = input.selectionStart;
            input.value = clean;
            if (caret != null && document.activeElement === input) {
                input.setSelectionRange(caret, caret);
            }
        }
        return clean;
    }

    /**
     * Modal listing the CSV attachments on the image. Resolves with the chosen
     * entry, or null if dismissed.
     * @param {Array}  csvs  entries from /list_omero_*_csvs
     * @param {Object} opts  {title, subtitle, tag} - tag, if given, badges the
     *                       entries this panel wrote (entry.is_gater); omit it
     *                       when the list is already restricted to one kind
     */
    function pickOmeroCsv(csvs, opts) {
        opts = opts || {};
        return new Promise(function (resolve) {
            var overlay = document.createElement('div');
            overlay.className = 'gater-picker-overlay';

            var box = document.createElement('div');
            box.className = 'gater-picker';

            var heading = document.createElement('h4');
            heading.textContent = opts.title || 'Load a CSV from OMERO';
            box.appendChild(heading);

            var sub = document.createElement('p');
            sub.className = 'gater-picker-sub';
            sub.textContent = opts.subtitle || 'CSV files attached to this image.';
            box.appendChild(sub);

            var list = document.createElement('div');
            list.className = 'gater-picker-list';

            // Built with textContent, not innerHTML: the filename and
            // description come from OMERO annotation metadata, which a user can
            // set, so interpolating them into markup would be an injection.
            csvs.forEach(function (c) {
                var item = document.createElement('button');
                item.type = 'button';
                item.className = 'gater-picker-item';

                var nameEl = document.createElement('span');
                nameEl.className = 'gater-picker-name';
                nameEl.textContent = c.name;
                // Only badge rows when the caller asked for it. The load
                // pickers list a single namespace, so every row would carry an
                // identical chip -- noise rather than information.
                if (opts.tag && c.is_gater) {
                    var tag = document.createElement('span');
                    tag.className = 'gater-picker-tag';
                    tag.textContent = opts.tag;
                    nameEl.appendChild(tag);
                }
                item.appendChild(nameEl);

                var kb = c.size ? Math.max(1, Math.round(c.size / 1024)) + ' KB' : '';
                // The description is the name the user gave the file, which is
                // normally identical to the filename -- only worth showing when
                // sanitising changed it.
                var label = (c.description && c.description !== c.name)
                    ? c.description : '';
                var metaEl = document.createElement('span');
                metaEl.className = 'gater-picker-meta';
                metaEl.textContent = [label, kb].filter(Boolean).join(' · ');
                item.appendChild(metaEl);

                item.onclick = function () { close(); resolve(c); };
                list.appendChild(item);
            });
            box.appendChild(list);

            var cancel = document.createElement('button');
            cancel.type = 'button';
            cancel.className = 'gater-picker-cancel';
            cancel.textContent = 'Cancel';
            cancel.onclick = function () { close(); resolve(null); };
            box.appendChild(cancel);

            function close() {
                document.removeEventListener('keydown', onKey);
                overlay.remove();
            }
            function onKey(e) {
                if (e.key === 'Escape') { close(); resolve(null); }
            }
            overlay.onclick = function (e) {
                if (e.target === overlay) { close(); resolve(null); }
            };
            document.addEventListener('keydown', onKey);

            overlay.appendChild(box);
            document.body.appendChild(overlay);
        });
    }

    /**
     * Styled yes/no dialog, so an overwrite prompt looks like the rest of the
     * OMERO CSV flow instead of a native browser confirm().
     * @returns {Promise<Boolean>} true if the primary action was chosen
     */
    function confirmDialog(title, message, okLabel) {
        return new Promise(function (resolve) {
            var overlay = document.createElement('div');
            overlay.className = 'gater-picker-overlay';

            var box = document.createElement('div');
            box.className = 'gater-picker';

            var heading = document.createElement('h4');
            heading.textContent = title;
            box.appendChild(heading);

            var msg = document.createElement('p');
            msg.className = 'gater-picker-warn';
            msg.textContent = message;
            box.appendChild(msg);

            var actions = document.createElement('div');
            actions.className = 'gater-picker-actions';
            var cancel = document.createElement('button');
            cancel.type = 'button';
            cancel.className = 'gater-picker-cancel';
            cancel.textContent = 'Cancel';
            var ok = document.createElement('button');
            ok.type = 'button';
            ok.className = 'gater-picker-save';
            ok.textContent = okLabel || 'OK';
            actions.appendChild(cancel);
            actions.appendChild(ok);
            box.appendChild(actions);

            function close() {
                document.removeEventListener('keydown', onKey);
                overlay.remove();
            }
            function onKey(e) {
                if (e.key === 'Escape') { close(); resolve(false); }
            }
            ok.onclick = function () { close(); resolve(true); };
            cancel.onclick = function () { close(); resolve(false); };
            overlay.onclick = function (e) {
                if (e.target === overlay) { close(); resolve(false); }
            };
            document.addEventListener('keydown', onKey);

            overlay.appendChild(box);
            document.body.appendChild(overlay);
            ok.focus();
        });
    }

    /**
     * Blocking "saving..." overlay. Deliberately NOT dismissible: writing the
     * per-cell gating export can take a minute, and without this users assume
     * the app has frozen, click again, and queue more saves -- which starves
     * the server's small thread pool and can OOM it.
     *
     * The bar is indeterminate because the server cannot report a percentage,
     * but the STAGE text and elapsed seconds are real: while open, this polls
     * /csv_save_status, which the save route updates as it works.
     *
     * @returns {Object} handle with .setStage(text) and .close()
     */
    function showSaveBusy(title, subtitle) {
        var overlay = document.createElement('div');
        overlay.className = 'gater-picker-overlay gater-busy-overlay';

        var box = document.createElement('div');
        box.className = 'gater-picker gater-busy';

        var heading = document.createElement('h4');
        heading.textContent = title || 'Saving to OMERO';
        box.appendChild(heading);

        var sub = document.createElement('p');
        sub.className = 'gater-picker-sub';
        sub.textContent = subtitle || '';
        box.appendChild(sub);

        var track = document.createElement('div');
        track.className = 'gater-busy-track';
        var bar = document.createElement('div');
        bar.className = 'gater-busy-bar';
        track.appendChild(bar);
        box.appendChild(track);

        var status = document.createElement('p');
        status.className = 'gater-busy-status';
        box.appendChild(status);

        var stage = '';
        var started = Date.now();
        function paint() {
            var secs = Math.round((Date.now() - started) / 1000);
            status.textContent = (stage || 'Working') + ' — ' + secs + 's';
        }
        paint();
        var tick = setInterval(paint, 1000);

        // Ask the server what it is actually doing. Failures are ignored: this
        // is decoration, and the save itself reports the real outcome.
        var poll = setInterval(function () {
            fetch('/csv_save_status')
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (s) {
                    if (s && s.active && s.stage) { stage = s.stage; paint(); }
                })
                .catch(function () { /* ignore */ });
        }, 1500);

        // Swallow clicks and Escape so the overlay cannot be dismissed.
        overlay.onclick = function (e) { e.stopPropagation(); };
        function block(e) {
            if (e.key === 'Escape') { e.stopPropagation(); e.preventDefault(); }
        }
        document.addEventListener('keydown', block, true);

        overlay.appendChild(box);
        document.body.appendChild(overlay);

        return {
            setStage: function (s) { stage = s; paint(); },
            close: function () {
                clearInterval(tick);
                clearInterval(poll);
                document.removeEventListener('keydown', block, true);
                overlay.remove();
            }
        };
    }

    window.GaterCsvDialogs = {
        sanitize: sanitize,
        canonical: canonical,
        sanitizeInputInPlace: sanitizeInputInPlace,
        pickOmeroCsv: pickOmeroCsv,
        confirmDialog: confirmDialog,
        showSaveBusy: showSaveBusy
    };
})();
