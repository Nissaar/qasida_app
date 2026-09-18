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

    function setUpFilterSidebar() {
        var filter = document.getElementById('changelist-filter');
        if (!filter) return;
        // Each filter is an <h3> followed by its list or form.
        filter.querySelectorAll('h3').forEach(function (heading, index) {
            var panel = heading.nextElementSibling;
            if (!panel || panel.tagName === 'H3') return;
            var label = (heading.textContent || ('filter-' + index)).trim();
            // Everything past the second group starts closed: five open groups
            // is what made this sidebar unusable.
            makeCollapsible(heading, panel, 'filter:' + label, index > 1);
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
})();
