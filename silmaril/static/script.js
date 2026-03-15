// Tree toggle
document.addEventListener('click', function(e) {
    const item = e.target.closest('.tree-dir > .tree-item');
    if (item) { e.preventDefault(); item.parentElement.classList.toggle('open'); }
});

// Sidebar toggle
const menuBtn = document.getElementById('sidebar-toggle');
const sidebar = document.querySelector('.sidebar');
const overlay = document.querySelector('.overlay');
const isMobile = () => window.innerWidth <= 768;
function toggleMenu(open) {
    if (isMobile()) {
        const o = open !== undefined ? open : !sidebar.classList.contains('open');
        sidebar.classList.toggle('open', o);
        overlay.classList.toggle('open', o);
        document.body.style.overflow = o ? 'hidden' : '';
    } else {
        const hidden = sidebar.classList.toggle('hidden');
        document.querySelector('.main-wrapper').style.marginLeft = hidden ? '0' : '';
    }
}
if (menuBtn) {
    menuBtn.addEventListener('click', () => toggleMenu());
    overlay.addEventListener('click', () => toggleMenu(false));
}

// Search
const si = document.getElementById('sidebar-search');
if (si) {
    let debounce;
    si.addEventListener('input', function() {
        clearTimeout(debounce);
        const q = this.value.trim();
        const items = document.querySelectorAll('.tree-file');
        const dirs = document.querySelectorAll('.tree-dir');
        if (!q) {
            items.forEach(i => i.style.display = '');
            dirs.forEach(d => { d.style.display = ''; d.classList.remove('open'); });
            document.querySelector('.search-results').innerHTML = '';
            return;
        }
        // Tree filter
        const ql = q.toLowerCase();
        items.forEach(i => {
            i.style.display = i.querySelector('.tree-item').textContent.toLowerCase().includes(ql) ? '' : 'none';
        });
        dirs.forEach(d => {
            const vis = d.querySelector('.tree-file:not([style*="display: none"])');
            d.style.display = vis ? '' : 'none';
            if (vis) d.classList.add('open');
        });
        // API search (debounced)
        if (q.length >= 2) {
            debounce = setTimeout(() => {
                fetch('/api/search?q=' + encodeURIComponent(q))
                    .then(r => r.json())
                    .then(results => {
                        const c = document.querySelector('.search-results');
                        if (!results.length) { c.innerHTML = '<div style="padding:6px 8px;color:var(--text2);font-size:12px;">Nothing found</div>'; return; }
                        c.innerHTML = results.slice(0, 15).map(r =>
                            '<a class="sr-item" href="/' + encodeURIComponent(r.path) + '">' +
                            '<div>' + r.name + '</div>' +
                            '<div class="sr-path">' + r.path + '</div>' +
                            (r.match ? '<div class="sr-match">...' + r.match + '...</div>' : '') + '</a>'
                        ).join('');
                    });
            }, 200);
        }
    });
}

// Tab in textarea
const ea = document.querySelector('.edit-area');
if (ea) {
    ea.addEventListener('keydown', function(e) {
        if (e.key === 'Tab') {
            e.preventDefault();
            const s = this.selectionStart, end = this.selectionEnd;
            this.value = this.value.substring(0, s) + '    ' + this.value.substring(end);
            this.selectionStart = this.selectionEnd = s + 4;
        }
        if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); this.closest('form').submit(); }
    });
}

// Close sidebar on nav (mobile)
document.querySelectorAll('.sidebar a').forEach(a => {
    a.addEventListener('click', () => { if (window.innerWidth <= 768) toggleMenu(false); });
});

// Copy buttons on code blocks
document.querySelectorAll('pre > code').forEach(function(block) {
    const pre = block.parentNode;
    // Wrap pre in a container for sticky button
    const wrap = document.createElement('div');
    wrap.style.cssText = 'position:relative;';
    pre.parentNode.insertBefore(wrap, pre);
    wrap.appendChild(pre);
    const btn = document.createElement('button');
    btn.className = 'copy-btn';
    btn.textContent = 'Copy';
    btn.style.cssText = 'position:absolute;top:6px;right:6px;padding:2px 8px;font-size:11px;background:var(--surface);border:1px solid var(--border);border-radius:3px;cursor:pointer;color:var(--text2);opacity:0;transition:opacity 0.15s;z-index:1;';
    wrap.appendChild(btn);
    wrap.addEventListener('mouseenter', () => btn.style.opacity = '1');
    wrap.addEventListener('mouseleave', () => btn.style.opacity = '0');
    btn.addEventListener('click', () => {
        navigator.clipboard.writeText(block.textContent).then(() => {
            btn.textContent = 'Copied!';
            setTimeout(() => btn.textContent = 'Copy', 1500);
        });
    });
});

// Initialize Lucide icons
if (typeof lucide !== 'undefined') lucide.createIcons();

// Icon Picker
(function() {
    var picker = document.getElementById('icon-picker');
    if (!picker) return;
    var pickerOverlay = document.getElementById('icon-picker-overlay');
    var grid = document.getElementById('icon-picker-grid');
    var searchInput = document.getElementById('icon-picker-search');
    var closeBtn = document.getElementById('icon-picker-close');
    var removeBtn = document.getElementById('icon-picker-remove');
    var colorInput = document.getElementById('icon-picker-color');
    var colorReset = document.getElementById('icon-picker-color-reset');
    var customInput = document.getElementById('icon-picker-custom');
    var customBtn = document.getElementById('icon-picker-custom-btn');
    var tabs = picker.querySelectorAll('.icon-picker-tab');
    var currentPath = '', activeTab = 'emoji', selectedColor = '';

    // PascalCase → kebab-case (e.g. FileText → file-text, AArrowDown → a-arrow-down)
    function toKebab(s) { return s.replace(/([a-z0-9])([A-Z])/g, '$1-$2').replace(/([A-Z]+)([A-Z][a-z])/g, '$1-$2').toLowerCase(); }

    var EMOJIS = [
        ['📁','folder'],['📂','folder open'],['📄','file document page'],['📝','memo note write'],
        ['📋','clipboard'],['📌','pin pushpin'],['📎','paperclip attach'],['🔗','link chain'],
        ['🔑','key'],['🔒','lock'],['🔓','unlock'],['💡','bulb idea light'],
        ['⚡','lightning zap bolt'],['🔥','fire hot flame'],['⭐','star'],['🌟','star glow'],
        ['✨','sparkles magic'],['💎','gem diamond'],['👑','crown king'],['🏆','trophy cup'],
        ['🎯','target dart bullseye'],['🎨','art palette paint'],['🎵','music note'],['🔔','bell notification'],
        ['❤️','heart love red'],['🧡','heart orange'],['💛','heart yellow'],['💚','heart green'],
        ['💙','heart blue'],['💜','heart purple'],['🖤','heart black'],['🤍','heart white'],
        ['✅','check done yes'],['❌','cross cancel no'],['⚠️','warning alert'],['🚫','forbidden'],
        ['🔴','red circle'],['🟡','yellow circle'],['🟢','green circle'],['🔵','blue circle'],
        ['⚪','white circle'],['⚫','black circle'],['🚀','rocket launch'],['✈️','airplane plane travel'],
        ['🌍','earth globe world'],['🌱','seedling grow'],['🌲','tree evergreen'],['🌸','flower blossom'],
        ['🍀','clover luck'],['☀️','sun sunny'],['🌙','moon night'],['🌈','rainbow'],
        ['🐶','dog'],['🐱','cat'],['🦊','fox'],['🐻','bear'],['🐼','panda'],['🦁','lion'],
        ['🐳','whale'],['🐉','dragon'],['🦄','unicorn'],['🦋','butterfly'],['🐝','bee'],
        ['🤖','robot bot'],['👻','ghost'],['💀','skull'],['👽','alien'],['🧠','brain mind'],
        ['👀','eyes look'],['👤','person user'],['👥','people group users'],
        ['📊','chart bar graph'],['📈','chart up trending'],['📉','chart down'],
        ['🗂️','dividers tabs'],['📦','package box'],['🏷️','label tag'],['🔖','bookmark'],
        ['✏️','pencil edit'],['🖊️','pen write'],['💻','laptop computer'],['🖥️','desktop monitor'],
        ['📱','phone mobile'],['📷','camera photo'],['🔬','microscope science'],['🔭','telescope'],
        ['📡','satellite'],['⚙️','gear settings cog'],['🔧','wrench tool'],['🔨','hammer build'],
        ['🧪','test tube flask'],['💊','pill medicine'],['♻️','recycle'],['💰','money bag'],
        ['💵','dollar cash'],['🎓','graduation cap education'],['📚','books library'],
        ['🏠','house home'],['🏢','building office'],['⏰','alarm clock time'],['🗓️','calendar date'],
    ];

    function apiUrl(path) { return '/api/icon/' + path.split('/').map(encodeURIComponent).join('/'); }

    function open(path) {
        currentPath = path; selectedColor = '';
        colorInput.value = '#000000'; searchInput.value = '';
        if (customInput) customInput.value = '';
        renderGrid();
        picker.classList.add('open'); pickerOverlay.classList.add('open');
        searchInput.focus();
    }
    function close() { picker.classList.remove('open'); pickerOverlay.classList.remove('open'); }

    function saveIcon(icon, color) {
        fetch(apiUrl(currentPath), {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({icon: icon, color: color || ''})
        }).then(function(r) { if (r.ok) location.reload(); });
    }

    function renderGrid() {
        var q = searchInput.value.toLowerCase().trim();
        grid.innerHTML = '';
        if (activeTab === 'emoji') {
            var items = q ? EMOJIS.filter(function(e) { return e[1].indexOf(q) >= 0; }) : EMOJIS;
            grid.innerHTML = items.map(function(e) {
                return '<button class="icon-pick" data-icon="' + e[0] + '" title="' + e[1] + '">' + e[0] + '</button>';
            }).join('');
        } else {
            if (typeof lucide === 'undefined' || !lucide.icons) {
                grid.innerHTML = '<div style="padding:12px;color:var(--text2)">Lucide not loaded</div>';
                return;
            }
            var names = Object.keys(lucide.icons).map(function(k) { return toKebab(k); }).sort();
            var filtered = q ? names.filter(function(n) { return n.indexOf(q) >= 0; }) : names;
            grid.innerHTML = filtered.slice(0, 200).map(function(n) {
                return '<button class="icon-pick" data-icon="lucide-' + n + '" title="' + n + '"><i data-lucide="' + n + '"></i></button>';
            }).join('');
            lucide.createIcons();
        }
    }

    tabs.forEach(function(tab) {
        tab.addEventListener('click', function() {
            tabs.forEach(function(t) { t.classList.remove('active'); });
            tab.classList.add('active');
            activeTab = tab.dataset.tab;
            searchInput.value = '';
            // Show/hide custom emoji input
            var customWrap = picker.querySelector('.icon-picker-custom');
            if (customWrap) customWrap.style.display = activeTab === 'emoji' ? 'flex' : 'none';
            renderGrid();
        });
    });

    var searchDebounce;
    searchInput.addEventListener('input', function() {
        clearTimeout(searchDebounce);
        searchDebounce = setTimeout(renderGrid, 150);
    });

    closeBtn.addEventListener('click', close);
    pickerOverlay.addEventListener('click', close);
    document.addEventListener('keydown', function(e) { if (e.key === 'Escape') close(); });

    grid.addEventListener('click', function(e) {
        var btn = e.target.closest('.icon-pick');
        if (btn) saveIcon(btn.dataset.icon, selectedColor);
    });

    if (customBtn) customBtn.addEventListener('click', function() {
        var v = customInput.value.trim();
        if (v) saveIcon(v, selectedColor);
    });
    if (customInput) customInput.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') { var v = this.value.trim(); if (v) saveIcon(v, selectedColor); }
    });

    removeBtn.addEventListener('click', function() {
        fetch(apiUrl(currentPath), {method: 'DELETE'}).then(function(r) { if (r.ok) location.reload(); });
    });

    colorInput.addEventListener('input', function(e) { selectedColor = e.target.value; });
    colorReset.addEventListener('click', function() { selectedColor = ''; colorInput.value = '#000000'; });

    document.querySelectorAll('[data-icon-path]').forEach(function(el) {
        el.addEventListener('click', function(e) { e.preventDefault(); e.stopPropagation(); open(el.dataset.iconPath); });
    });
})();

// KaTeX auto-render
document.addEventListener('DOMContentLoaded', function() {
    if (typeof renderMathInElement !== 'undefined') {
        renderMathInElement(document.body, {
            delimiters: [
                {left: '$$', right: '$$', display: true},
                {left: '$', right: '$', display: false}
            ],
            throwOnError: false
        });
    }
});
