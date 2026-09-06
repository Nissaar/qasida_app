/*
 * Collapsible sections for the admin.
 *
 * Django renders the left nav and the filter sidebar as flat lists. This turns
 * each group into a disclosure that remembers whether it was open, so long
 * lists (58 tags, five filter groups) stop pushing everything off-screen.
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
            var caption = module.querySelector('caption, th[scope="col"], h2');
            if (!caption) return;
            // The rows live in the table body next to the caption.
            var panel = module.querySelector('tbody') || module.querySelector('ul');
            var label = (caption.textContent || ('app-' + index)).trim();
            makeCollapsible(caption, panel, 'nav:' + label, false);
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
        setUpFilterSidebar();
        setUpFieldsets();
        setUpLiveSearch();
    });
})();
