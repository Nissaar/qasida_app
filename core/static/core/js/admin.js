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

    document.addEventListener('DOMContentLoaded', function () {
        setUpNavSidebar();
        setUpFilterSidebar();
        setUpFieldsets();
    });
})();
