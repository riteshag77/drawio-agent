function effectiveSidebarMax() {
    const cssMax = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-max-width'));
    const cssMin = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-min-width'));
    const logPanel = document.getElementById('log-panel');
    const logWidth = logPanel && logPanel.classList.contains('active') ? logPanel.getBoundingClientRect().width : 0;
    const reservedForDiagram = 360;
    const viewportCeiling = window.innerWidth - logWidth - reservedForDiagram;
    return Math.max(cssMin, Math.min(cssMax, viewportCeiling));
}

function toggleSidebarCollapse() {
    const sidebar = document.querySelector('.sidebar');
    if (!sidebar) return;
    const collapsed = sidebar.classList.toggle('collapsed');
    document.body.classList.toggle('sidebar-collapsed', collapsed);
    localStorage.setItem(SIDEBAR_COLLAPSED_KEY, collapsed ? '1' : '0');
}

function initSidebarCollapse() {
    const sidebar = document.querySelector('.sidebar');
    if (!sidebar) return;
    const collapsed = localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === '1';
    sidebar.classList.toggle('collapsed', collapsed);
    document.body.classList.toggle('sidebar-collapsed', collapsed);
}

function initSidebarWidth() {
    const stored = localStorage.getItem(SIDEBAR_WIDTH_KEY);
    if (!stored) return;
    const min = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-min-width'));
    const width = Math.max(min, Math.min(parseInt(stored), effectiveSidebarMax()));
    document.documentElement.style.setProperty('--sidebar-width', width + 'px');
}

(function initSidebarResizer() {
    const resizer = document.getElementById('sidebar-resizer');
    const sidebar = document.querySelector('.sidebar');
    let startX, startWidth;

    function persistWidth(width) {
        localStorage.setItem(SIDEBAR_WIDTH_KEY, Math.round(width) + 'px');
    }

    function onMouseDown(e) {
        startX = e.clientX;
        startWidth = sidebar.getBoundingClientRect().width;
        resizer.classList.add('resizing');
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
        document.documentElement.style.pointerEvents = 'none';
        resizer.style.pointerEvents = 'all';
        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('mouseup', onMouseUp);
        e.preventDefault();
    }

    function onMouseMove(e) {
        const delta = e.clientX - startX;
        const min = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-min-width'));
        const newWidth = Math.min(Math.max(startWidth + delta, min), effectiveSidebarMax());
        document.documentElement.style.setProperty('--sidebar-width', newWidth + 'px');
    }

    function onMouseUp() {
        resizer.classList.remove('resizing');
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        document.documentElement.style.pointerEvents = '';
        document.removeEventListener('mousemove', onMouseMove);
        document.removeEventListener('mouseup', onMouseUp);
        persistWidth(parseFloat(getComputedStyle(sidebar).width));
    }

    resizer.addEventListener('mousedown', onMouseDown);

    resizer.addEventListener('keydown', (e) => {
        const step = e.shiftKey ? 20 : 10;
        const min = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-min-width'));
        const max = effectiveSidebarMax();
        let current = parseFloat(getComputedStyle(sidebar).width);
        if (e.key === 'ArrowLeft') {
            current = Math.max(min, current - step);
            document.documentElement.style.setProperty('--sidebar-width', current + 'px');
            persistWidth(current);
            e.preventDefault();
        } else if (e.key === 'ArrowRight') {
            current = Math.min(max, current + step);
            document.documentElement.style.setProperty('--sidebar-width', current + 'px');
            persistWidth(current);
            e.preventDefault();
        }
    });

    initSidebarWidth();
    initSidebarCollapse();
})();
