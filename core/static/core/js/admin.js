/*
 * The admin's behaviour, beyond what Django ships.
 *
 *   - Collapsible groups in the left nav, the filter rail and inline formsets,
 *     each remembering whether it was open. Django renders them as flat lists,
 *     and a flat list of 58 tags or five filter groups pushes everything else
 *     off the screen.
 *   - The sidebar's own quick filter. Django's version selects rows by
 *     `th[scope=row] a`, which is the table markup this project replaced with
 *     a list; its own handler now matches nothing, so this one does the work.
 *   - Searching a changelist as you type.
 *   - Status columns drawn as pills rather than as one more grey word.
 *   - Pressing / to reach the search box.
 */
(function () {
    'use strict';

    var STORE_PREFIX = 'qadmin:collapsed:';

    function remembered(key) {
        try {
            return localStorage.getItem(STORE_PREFIX + key) === '1';
        } catch (e) {
            return false;  // private mode: default to open
        }
    }

    function remember(key, collapsed) {
        try {
            localStorage.setItem(STORE_PREFIX + key, collapsed ? '1' : '0');
        } catch (e) {
            /* nothing to do */
        }
    }

    /** Make `header` toggle `panel`, keyed by `key` for persistence. */
    function makeCollapsible(header, panel, key, startCollapsed) {
        if (!header || !panel || header.dataset.qCollapsible) return;
        header.dataset.qCollapsible = '1';
        header.classList.add('q-collapsible');
        header.setAttribute('role', 'button');
        header.setAttribute('tabindex', '0');

        function apply(collapsed) {
            panel.hidden = collapsed;
            header.classList.toggle('q-collapsed', collapsed);
            header.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        }

        var collapsed = remembered(key) || (startCollapsed && localStorage.getItem(STORE_PREFIX + key) === null);
        apply(collapsed);

        function toggle() {
            collapsed = !collapsed;
            apply(collapsed);
            remember(key, collapsed);
        }

        header.addEventListener('click', toggle);
        header.addEventListener('keydown', function (event) {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                toggle();
            }
        });
    }

    function setUpNavSidebar() {
        var sidebar = document.getElementById('nav-sidebar');
        if (!sidebar) return;
        sidebar.querySelectorAll('.module').forEach(function (module, index) {
            var heading = module.querySelector('h2, caption, th[scope="col"]');
            if (!heading) return;
            var panel = module.querySelector('ul') || module.querySelector('tbody');
            var label = (heading.textContent || ('app-' + index)).trim();
            makeCollapsible(heading, panel, 'nav:' + label, false);
        });
    }

    /*
     * Narrow the sidebar to whatever is typed in it.
     *
     * Django's own version of this reads the stock table markup, which the
     * app_list template here replaces with a list; its handler therefore finds
     * no rows and does nothing but leave a "no results" class behind, which
     * this clears. Groups left with nothing showing are hidden too, so
     * filtering does not leave a column of empty headings.
     */
    function setUpNavFilter() {
        var input = document.getElementById('nav-filter');
        var sidebar = document.getElementById('nav-sidebar');
        if (!input || !sidebar) return;

        var groups = Array.prototype.map.call(
            sidebar.querySelectorAll('.module'), function (group) {
                return {
                    node: group,
                    items: Array.prototype.map.call(
                        group.querySelectorAll('.q-nav-list > li'), function (item) {
                            return {node: item, text: (item.textContent || '').toLowerCase()};
                        }),
                };
            });
        if (!groups.length) return;  // stock table markup: leave Django to it

        function apply() {
            var needle = (input.value || '').trim().toLowerCase();
            var anyShown = false;
            groups.forEach(function (group) {
                var shown = 0;
                group.items.forEach(function (item) {
                    var hit = !needle || item.text.indexOf(needle) !== -1;
                    item.node.style.display = hit ? '' : 'none';
                    if (hit) shown += 1;
                });
                group.node.style.display = (needle && !shown) ? 'none' : '';
                anyShown = anyShown || shown > 0;
            });
            input.classList.toggle('no-results', Boolean(needle) && !anyShown);
            try {
                sessionStorage.setItem('django.admin.navSidebarFilterValue', needle);
            } catch (e) { /* private mode: the filter simply does not persist */ }
        }

        input.addEventListener('input', apply);
        input.addEventListener('keyup', function (event) {
            if (event.key === 'Escape') {
                input.value = '';
                apply();
            }
        });

        try {
            var stored = sessionStorage.getItem('django.admin.navSidebarFilterValue');
            if (stored) input.value = stored;
        } catch (e) { /* nothing stored */ }
        apply();
    }

    /*
     * Draw the state columns as pills.
     *
     * A changelist of a hundred rows is scanned for the exceptions in it -
     * what is still pending, what is rejected, which text cannot be trusted -
     * and a word set in the same grey as every other word does not answer
     * that. The text is left exactly as Django rendered it; only its
     * appearance changes, so sorting, filtering and the column headings are
     * untouched.
     *
     * Matched on the value rather than on the column, so a state that is
     * added later needs nothing here, and a column this does not recognise is
     * left alone rather than guessed at.
     */
    var PILL_COLUMNS = ['review_state', 'status', 'text_quality', 'kind',
                        'translation_origin', 'topic'];
    var PILL_TONES = [
        [/^(approved|accepted|extracted)/i, 'q-pill-good'],
        [/^(awaiting|waiting|pending)/i, 'q-pill-wait'],
        [/^(rejected|declined|unreliable)/i, 'q-pill-bad'],
    ];

    function toneFor(text) {
        for (var i = 0; i < PILL_TONES.length; i += 1) {
            if (PILL_TONES[i][0].test(text)) return PILL_TONES[i][1];
        }
        return 'q-pill-info';
    }

    function setUpStatusPills() {
        var table = document.getElementById('result_list');
        if (!table) return;
        PILL_COLUMNS.forEach(function (column) {
            table.querySelectorAll('td.field-' + column).forEach(function (cell) {
                // A cell holding a control - list_editable renders a select
                // here - is left alone; a pill would hide the thing that is
                // meant to be used.
                if (cell.querySelector('input, select, textarea, a')) return;
                var text = (cell.textContent || '').trim();
                if (!text || text === '-' || text === '\u2014') return;
                var pill = document.createElement('span');
                pill.className = 'q-pill ' + toneFor(text);
                pill.textContent = text;
                cell.textContent = '';
                cell.appendChild(pill);
            });
        });
    }

    /*
     * Press / to reach the search box.
     *
     * Only when nothing else is focused, so it never swallows a slash typed
     * into a field - which on this site means a date, a URL, or a poet whose
     * name is two names joined with one.
     */
    function setUpSearchShortcut() {
        document.addEventListener('keydown', function (event) {
            if (event.key !== '/' || event.metaKey || event.ctrlKey || event.altKey) return;
            var active = document.activeElement;
            if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' ||
                           active.tagName === 'SELECT' || active.isContentEditable)) return;
            var box = document.getElementById('searchbar') || document.getElementById('nav-filter');
            if (!box) return;
            event.preventDefault();
            box.focus();
            box.select();
        });
    }

    /*
     * Remember which filter groups are open, and start the later ones closed.
     *
     * Django renders each filter as <details><summary> and marks every one of
     * them open. On the qasida list that is six groups at once - one of them
     * every dedication in the library - so the rail runs several screens past
     * the results it is meant to narrow, and reopens itself on every page you
     * visit.
     *
     * This used to be done by makeCollapsible over the <h3> headings Django
     * rendered before 5.0; that markup is gone, the selector matched nothing,
     * and the feature had quietly stopped existing. The disclosure is native
     * now, so this only decides the starting state and remembers what you do
     * with it. A group holding a chosen value always opens, whatever was
     * remembered: hiding the filter that is currently in force is how someone
     * ends up unable to find why a list is short.
     */
    var FILTER_OPEN_BY_DEFAULT = 2;

    function setUpFilterSidebar() {
        var filter = document.getElementById('changelist-filter');
        if (!filter) return;

        filter.querySelectorAll('details').forEach(function (group, index) {
            var summary = group.querySelector('summary');
            var label = group.dataset.filterTitle ||
                        (summary && summary.textContent.trim()) || ('filter-' + index);
            var key = 'filter:' + label;
            var stored = null;
            try {
                stored = localStorage.getItem(STORE_PREFIX + key);
            } catch (e) { /* private mode: fall back to the defaults below */ }

            var active = group.querySelector('li.selected');
            if (active) {
                group.open = true;
            } else if (stored !== null) {
                group.open = stored !== '1';
            } else {
                group.open = index < FILTER_OPEN_BY_DEFAULT;
            }

            group.addEventListener('toggle', function () {
                remember(key, !group.open);
            });
        });
    }

    function setUpFieldsets() {
        document.querySelectorAll('.inline-group > h2').forEach(function (heading, index) {
            var group = heading.parentElement;
            var panel = document.createElement('div');
            panel.className = 'q-inline-body';
            var moved = Array.prototype.slice.call(group.children).filter(function (node) {
                return node !== heading;
            });
            if (!moved.length) return;
            moved.forEach(function (node) { panel.appendChild(node); });
            group.appendChild(panel);
            makeCollapsible(heading, panel, 'inline:' + (heading.textContent || index).trim(), false);
        });
    }

    /*
     * Search as you type on a changelist.
     *
     * The admin's search needs the button pressed. This submits the form for
     * you a short pause after you stop typing, so the list narrows while you
     * work. Submitting rather than swapping the table in place is deliberate:
     * the results carry Django's own action checkboxes and pagination, and
     * replacing that markup by hand would break the wiring behind them.
     *
     * Submitting reloads the page, which would take the cursor with it, so
     * where the cursor was is carried across and restored.
     */
    var SEARCH_DELAY = 450;
    var FOCUS_FLAG = 'qadmin:refocus-search';

    function setUpLiveSearch() {
        var input = document.getElementById('searchbar');
        if (!input) return;
        var form = input.form;
        if (!form) return;

        // What the page was already searched for, so an unchanged query - a
        // stray keypress, or arriving back here - never reloads for nothing.
        var applied = input.value;
        var timer = null;

        input.addEventListener('input', function () {
            clearTimeout(timer);
            timer = setTimeout(function () {
                var query = input.value.trim();
                if (query === applied.trim()) return;
                // A single letter matches most of the library and is almost
                // never what someone means to stop on.
                if (query.length === 1) return;
                try {
                    sessionStorage.setItem(FOCUS_FLAG, String(input.selectionStart));
                } catch (e) { /* private mode: the cursor simply moves to the end */ }
                form.submit();
            }, SEARCH_DELAY);
        });

        // Enter should search now rather than wait out the timer.
        input.addEventListener('keydown', function (event) {
            if (event.key === 'Enter') clearTimeout(timer);
        });

        restoreFocus(input);
    }

    function restoreFocus(input) {
        var at;
        try {
            at = sessionStorage.getItem(FOCUS_FLAG);
            sessionStorage.removeItem(FOCUS_FLAG);
        } catch (e) {
            return;
        }
        if (at === null) return;
        input.focus();
        var position = parseInt(at, 10);
        if (isNaN(position) || position > input.value.length) position = input.value.length;
        try {
            input.setSelectionRange(position, position);
        } catch (e) { /* some input types refuse; focus alone is enough */ }
    }

    document.addEventListener('DOMContentLoaded', function () {
        setUpNavSidebar();
        setUpNavFilter();
        setUpFilterSidebar();
        setUpFieldsets();
        setUpLiveSearch();
        setUpStatusPills();
        setUpSearchShortcut();
    });

    /*
     * The qasida form, in sections that fold. An editor works through a long
     * form and folds away what is done, so what they fold is remembered for
     * that work only: the next work opens with everything showing. A folded
     * Tags section still says how many are ticked.
     */
    (function setUpSections() {
        var sections = document.querySelectorAll('#content-main fieldset.q-form-section');
        if (!sections.length) return;
        var headings = [];
        sections.forEach(function (fieldset, index) {
            var heading = fieldset.querySelector('h2');
            if (!heading) return;
            var panel = document.createElement('div');
            panel.className = 'q-section-body';
            Array.prototype.slice.call(fieldset.children).forEach(function (node) {
                if (node !== heading && node.tagName !== 'LEGEND') panel.appendChild(node);
            });
            fieldset.appendChild(panel);

            var boxes = panel.querySelectorAll('div.q-tag-choices input[type="checkbox"]');
            if (boxes.length) {
                var note = document.createElement('span');
                note.className = 'q-section-note';
                heading.appendChild(note);
                var count = function () {
                    var ticked = panel.querySelectorAll('div.q-tag-choices input:checked').length;
                    note.textContent = ticked + ' chosen';
                };
                panel.addEventListener('change', count);
                count();
            }
            var title = (heading.firstChild && heading.firstChild.textContent || String(index)).trim();
            makeCollapsible(heading, panel, 'section:' + location.pathname + ':' + title, false);
            headings.push(heading);
        });

        if (headings.length < 2) return;

        // One tab at a time. The sections stay in the page (so every field still posts); the others are hidden.
        var holder = sections[0].parentElement;
        var tabs = document.createElement('div');
        tabs.className = 'q-tabs';
        tabs.setAttribute('role', 'tablist');
        var buttons = [];
        // Recordings and scanned pages are inlines outside the fieldsets; they get a tab of their own.
        var inlines = Array.prototype.slice.call(holder.querySelectorAll('.inline-group'));
        function show(index) {
            sections.forEach(function (fieldset, i) { fieldset.hidden = i !== index; });
            inlines.forEach(function (group) { group.hidden = index !== sections.length; });
            buttons.forEach(function (button, i) { button.setAttribute('aria-selected', i === index ? 'true' : 'false'); });
            try { history.replaceState(null, '', '#' + buttons[index].dataset.slug); } catch (e) { /* ignore */ }
        }
        var firstWithError = -1;
        sections.forEach(function (fieldset, index) {
            var heading = fieldset.querySelector('h2');
            var label = heading ? (heading.firstChild && heading.firstChild.textContent || '').trim() : 'Section ' + (index + 1);
            var button = document.createElement('button');
            button.type = 'button';
            button.className = 'q-tab';
            button.setAttribute('role', 'tab');
            button.dataset.slug = label.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || String(index);
            button.textContent = label;
            var note = heading && heading.querySelector('.q-section-note');
            if (note) {
                var badge = document.createElement('span');
                badge.className = 'q-tab-badge';
                button.appendChild(badge);
                var sync = function () { badge.textContent = note.textContent; };
                fieldset.addEventListener('change', sync);
                sync();
            }
            if (fieldset.querySelector('.errorlist, .errors')) {
                button.classList.add('q-tab-error');
                if (firstWithError < 0) firstWithError = index;
            }
            button.addEventListener('click', function () { show(index); });
            buttons.push(button);
            tabs.appendChild(button);
        });
        if (inlines.length) {
            var media = document.createElement('button');
            media.type = 'button';
            media.className = 'q-tab';
            media.setAttribute('role', 'tab');
            media.dataset.slug = 'recordings-and-pages';
            media.textContent = 'Recordings and pages';
            if (inlines.some(function (group) { return group.querySelector('.errorlist, .errors'); })) {
                media.classList.add('q-tab-error');
                if (firstWithError < 0) firstWithError = sections.length;
            }
            media.addEventListener('click', function () { show(sections.length); });
            buttons.push(media);
            tabs.appendChild(media);
        }
        holder.insertBefore(tabs, sections[0]);
        holder.classList.add('q-tabs-on');
        var wanted = buttons.findIndex(function (button) { return '#' + button.dataset.slug === location.hash; });
        show(firstWithError >= 0 ? firstWithError : (wanted >= 0 ? wanted : 0));
    })();

    /*
     * The list's filters live in a drawer, so the table has the whole width.
     * A Filters button opens it, the active filters show as chips above the
     * table that each undo themselves, and Esc or a click outside closes it.
     */
    (function setUpFilterDrawer() {
        var drawer = document.getElementById('changelist-filter');
        var toolbar = document.getElementById('toolbar');
        if (!drawer || !toolbar) return;
        var ignore = {p: 1, o: 1, q: 1, _changelist_filters: 1, all: 1, e: 1, _popup: 1, _to_field: 1};
        var url = new URL(window.location.href);
        var active = [];
        url.searchParams.forEach(function (value, key) { if (!ignore[key] && value !== '') active.push([key, value]); });

        var bar = document.createElement('div');
        bar.className = 'q-filter-bar';
        var button = document.createElement('button');
        button.type = 'button';
        button.className = 'q-btn-ghost q-filter-button';
        button.setAttribute('aria-expanded', 'false');
        button.setAttribute('aria-controls', 'changelist-filter');
        button.textContent = 'Filters';
        if (active.length) { var n = document.createElement('b'); n.textContent = String(active.length); button.appendChild(n); }
        bar.appendChild(button);

        function nice(key) { return key.replace(/__(exact|icontains|id__exact|isnull|gte|lte)$/, '').replace(/_/g, ' '); }
        active.forEach(function (pair) {
            var chip = document.createElement('span');
            chip.className = 'q-chip';
            chip.appendChild(document.createTextNode(nice(pair[0]) + ': ' + pair[1]));
            var off = new URL(window.location.href);
            off.searchParams.delete(pair[0]); off.searchParams.delete('p');
            var link = document.createElement('a');
            link.href = off.pathname + off.search;
            link.setAttribute('aria-label', 'Remove filter ' + nice(pair[0]));
            link.textContent = '\u00d7';
            chip.appendChild(link);
            bar.appendChild(chip);
        });
        if (active.length > 1) {
            var clear = document.createElement('a');
            clear.className = 'q-chip-clear';
            var base = new URL(window.location.href);
            ['q'].forEach(function () {});
            var keep = new URLSearchParams();
            if (url.searchParams.get('q')) keep.set('q', url.searchParams.get('q'));
            clear.href = base.pathname + (keep.toString() ? '?' + keep.toString() : '');
            clear.textContent = 'Clear all';
            bar.appendChild(clear);
        }
        toolbar.insertAdjacentElement('afterend', bar);

        var close = document.createElement('button');
        close.type = 'button';
        close.className = 'q-btn-ghost q-filter-close';
        close.textContent = 'Close';
        drawer.insertBefore(close, drawer.firstChild);

        function set(open) {
            document.body.classList.toggle('q-filters-open', open);
            button.setAttribute('aria-expanded', open ? 'true' : 'false');
            if (open) close.focus(); else if (document.activeElement && drawer.contains(document.activeElement)) button.focus();
        }
        button.addEventListener('click', function () { set(!document.body.classList.contains('q-filters-open')); });
        close.addEventListener('click', function () { set(false); });
        document.addEventListener('keydown', function (event) { if (event.key === 'Escape') set(false); });
        document.addEventListener('click', function (event) {
            if (document.body.classList.contains('q-filters-open') && !drawer.contains(event.target) && !button.contains(event.target)) set(false);
        });
    })();

    /*
     * "Translate from the lyrics", beside the translation box. Sends the
     * lyrics as they stand in the form - saved or not - to be translated on
     * the worker, asks after the job until the draft is ready, and puts it in
     * the box marked as a machine translation. Nothing is saved until the
     * editor presses Save, having read it.
     */
    (function setUpTranslateButton() {
        var config = document.getElementById('q-translate-config');
        var target = document.getElementById('id_translation');
        var lyrics = document.getElementById('id_lyrics');
        if (!config || !target || !lyrics) return;

        var bar = document.createElement('div');
        bar.className = 'q-translate-bar';
        var button = document.createElement('button');
        button.type = 'button';
        button.className = 'q-btn-ghost';
        button.textContent = 'Translate from the lyrics';
        var status = document.createElement('span');
        status.className = 'q-translate-status';
        status.setAttribute('role', 'status');
        status.setAttribute('aria-live', 'polite');
        var models = (config.dataset.models || '').split(',').filter(Boolean);
        var picker = null;
        if (models.length > 1) {
            picker = document.createElement('select');
            picker.setAttribute('aria-label', 'Gemini model');
            var auto = document.createElement('option');
            auto.value = ''; auto.textContent = 'Model: automatic (' + models.join(' \u2192 ') + ')';
            picker.appendChild(auto);
            models.forEach(function (name) {
                var option = document.createElement('option');
                option.value = name; option.textContent = name;
                picker.appendChild(option);
            });
            bar.appendChild(picker);
        }
        bar.appendChild(button);
        bar.appendChild(status);
        target.insertAdjacentElement('afterend', bar);

        // "Write in the original script": the transliteration (or Latin lyrics) back into Arabic, Urdu ...
        var scriptButton = null;
        if (config.dataset.toScript) {
            scriptButton = document.createElement('button');
            scriptButton.type = 'button';
            scriptButton.className = 'q-btn-ghost';
            scriptButton.textContent = 'Write the lyrics in the original script';
            button.insertAdjacentElement('afterend', scriptButton);
        }

        function csrf() {
            var field = document.querySelector('input[name=csrfmiddlewaretoken]');
            return field ? field.value : '';
        }
        function say(text, isError) {
            status.textContent = text;
            status.classList.toggle('q-translate-error', !!isError);
        }
        function finish() { button.disabled = false; if (scriptButton) scriptButton.disabled = false; }

        if (scriptButton) scriptButton.addEventListener('click', function () {
            var romanised = document.getElementById('id_transliteration');
            var language = document.getElementById('id_language');
            var source = romanised && romanised.value.trim() ? romanised.value : lyrics.value;
            if (!source.trim()) { say('The transliteration box is empty.', true); return; }
            if (!language || !language.value.trim()) { say('Fill in the Language field first.', true); return; }
            if (lyrics.value.trim() && !window.confirm(
                    'Replace the lyrics already in the box with a machine draft in the original script?')) return;
            var body = new URLSearchParams();
            body.append('text', source);
            body.append('language', language.value);
            body.append('model', picker ? picker.value : '');
            button.disabled = true; scriptButton.disabled = true;
            say('Starting\u2026');
            fetch(config.dataset.toScript, {
                method: 'POST', credentials: 'same-origin', body: body, headers: {'X-CSRFToken': csrf()}
            }).then(function (response) {
                return response.json().then(function (data) { return [response.ok, data]; });
            }).then(function (pair) {
                var data = pair[1];
                if (!pair[0]) { say(data.error || 'Could not start.', true); finish(); return; }
                var started = Date.now();
                (function poll() {
                    fetch(config.dataset.status.replace('JOB', data.job), {credentials: 'same-origin'})
                        .then(function (response) { return response.json(); })
                        .then(function (job) {
                            if (job.state === 'done') {
                                lyrics.value = job.script;
                                lyrics.dispatchEvent(new Event('input', {bubbles: true}));
                                say('Written in ' + data.language + ' script with ' + job.engine +
                                    '. A machine draft: read it through before saving.');
                                finish();
                            } else if (job.state === 'failed') {
                                say(job.error, true); finish();
                            } else if (Date.now() - started > 10 * 60 * 1000) {
                                say('Still not done after ten minutes. Is the worker running?', true); finish();
                            } else {
                                say(data.note + ' Working\u2026 ' + Math.round((Date.now() - started) / 1000) + 's');
                                setTimeout(poll, 1500);
                            }
                        }).catch(function () { setTimeout(poll, 3000); });
                })();
            }).catch(function () { say('Could not reach the server.', true); finish(); });
        });

        button.addEventListener('click', function () {
            if (!lyrics.value.trim()) { say('The lyrics box is empty.', true); return; }
            if (target.value.trim() && !window.confirm(
                    'Replace the translation already in the box with a machine draft?')) return;

            var language = document.getElementById('id_language');
            var body = new URLSearchParams();
            body.append('lyrics', lyrics.value);
            body.append('language', language ? language.value : '');
            body.append('model', picker ? picker.value : '');
            button.disabled = true; if (scriptButton) scriptButton.disabled = true;
            say('Working out the language\u2026');

            fetch(config.dataset.start, {
                method: 'POST', credentials: 'same-origin', body: body,
                headers: {'X-CSRFToken': csrf()}
            }).then(function (response) {
                return response.json().then(function (data) { return [response.ok, data]; });
            }).then(function (pair) {
                var data = pair[1];
                if (!pair[0]) { say(data.error || 'Could not start.', true); finish(); return; }
                var started = Date.now();
                say(data.note + ' Translating with ' + data.engine + '\u2026');
                (function poll() {
                    fetch(config.dataset.status.replace('JOB', data.job), {credentials: 'same-origin'})
                        .then(function (response) { return response.json(); })
                        .then(function (job) {
                            if (job.state === 'done') {
                                target.value = job.translation;
                                target.dispatchEvent(new Event('input', {bubbles: true}));
                                var romanised = document.getElementById('id_transliteration');
                                if (romanised && job.transliteration && !romanised.value.trim()) {
                                    romanised.value = job.transliteration;
                                    romanised.dispatchEvent(new Event('input', {bubbles: true}));
                                }
                                var origin = document.getElementById('id_translation_origin');
                                if (origin) origin.value = config.dataset.machine;
                                say('Translated from ' + data.language + ' with ' + job.engine +
                                    '. A machine draft: read it through before saving.');
                                finish();
                            } else if (job.state === 'failed') {
                                say(job.error, true); finish();
                            } else if (Date.now() - started > 10 * 60 * 1000) {
                                say('Still not done after ten minutes. Is the worker running?', true);
                                finish();
                            } else {
                                var seconds = Math.round((Date.now() - started) / 1000);
                                say(data.note + ' Translating with ' + data.engine +
                                    '\u2026 ' + seconds + 's');
                                setTimeout(poll, 1500);
                            }
                        }).catch(function () { setTimeout(poll, 3000); });
                })();
            }).catch(function () { say('Could not reach the server.', true); finish(); });
        });
    })();

    /*
     * The duplicate comparison: the two copies scroll as one, so the stanza
     * being read on one side is the stanza shown on the other. The two are
     * rarely the same length, so each follows the other's position as a
     * share of its own height rather than by pixels. A switch turns it off,
     * and is remembered.
     */
    document.querySelectorAll('.q-dup-compare').forEach(function (compare) {
        var panes = compare.querySelectorAll('.q-dup-text');
        if (panes.length !== 2) return;
        var toggle = compare.parentElement.querySelector('[data-dup-sync]');
        var STORE = 'qadmin:dup-sync';
        try {
            if (toggle && localStorage.getItem(STORE) === '0') toggle.checked = false;
        } catch (e) { /* private mode: keep the default */ }
        if (toggle) {
            toggle.addEventListener('change', function () {
                try { localStorage.setItem(STORE, toggle.checked ? '1' : '0'); } catch (e) { /* ignore */ }
            });
        }

        // Which pane the reader is moving. The other's scroll events are the
        // echo of ours and must not be sent back, or the two fight.
        var leader = null, release = null;
        function follow(from, to) {
            if (toggle && !toggle.checked) return;
            if (leader && leader !== from) return;
            leader = from;
            var range = from.scrollHeight - from.clientHeight;
            var share = range > 0 ? from.scrollTop / range : 0;
            to.scrollTop = share * (to.scrollHeight - to.clientHeight);
            clearTimeout(release);
            release = setTimeout(function () { leader = null; }, 120);
        }
        panes[0].addEventListener('scroll', function () { follow(panes[0], panes[1]); }, { passive: true });
        panes[1].addEventListener('scroll', function () { follow(panes[1], panes[0]); }, { passive: true });
    });

    /* A ruling moves or hides a work, so each button asks first. */
    document.querySelectorAll('button[data-confirm]').forEach(function (button) {
        button.addEventListener('click', function (event) {
            if (!window.confirm(button.getAttribute('data-confirm'))) event.preventDefault();
        });
    });
})();
