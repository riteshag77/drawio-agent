(function initSidebarResizer() {
    const resizer = document.getElementById('sidebar-resizer');
    const sidebar = document.querySelector('.sidebar');
    let startX, startWidth;

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
        const newWidth = Math.min(Math.max(startWidth + delta, parseInt(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-min-width'))), parseInt(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-max-width')));
        document.documentElement.style.setProperty('--sidebar-width', newWidth + 'px');
    }

    function onMouseUp() {
        resizer.classList.remove('resizing');
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        document.documentElement.style.pointerEvents = '';
        document.removeEventListener('mousemove', onMouseMove);
        document.removeEventListener('mouseup', onMouseUp);
    }

    resizer.addEventListener('mousedown', onMouseDown);

    resizer.addEventListener('keydown', (e) => {
        const step = e.shiftKey ? 20 : 10;
        const min = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-min-width'));
        const max = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-max-width'));
        let current = parseFloat(getComputedStyle(sidebar).width);
        if (e.key === 'ArrowLeft') {
            current = Math.max(min, current - step);
            document.documentElement.style.setProperty('--sidebar-width', current + 'px');
            e.preventDefault();
        } else if (e.key === 'ArrowRight') {
            current = Math.min(max, current + step);
            document.documentElement.style.setProperty('--sidebar-width', current + 'px');
            e.preventDefault();
        }
    });
})();
