/** App-wide design-token theme (sidebar/canvas/insights chrome) — separate
 *  concern from THEME_STORAGE_KEY in diagram.js, which only re-skins the
 *  embedded Draw.io canvas iframe. */

function changeAppTheme() {
    const select = document.getElementById('app-theme-select');
    const theme = APP_THEMES.includes(select.value) ? select.value : APP_THEMES[0];
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem(APP_THEME_KEY, theme);
    if (typeof applyDrawioTheme === 'function') {
        applyDrawioTheme(APP_THEME_TO_DRAWIO_THEME[theme]);
    }
}

function initAppTheme() {
    const stored = localStorage.getItem(APP_THEME_KEY);
    const theme = APP_THEMES.includes(stored) ? stored : APP_THEMES[0];
    document.documentElement.setAttribute('data-theme', theme);
    const select = document.getElementById('app-theme-select');
    if (select) select.value = theme;
    // ensureDrawioFrame() (called later in app.js's boot sequence) reads
    // THEME_STORAGE_KEY fresh, so writing it here is enough for the initial load.
    localStorage.setItem(THEME_STORAGE_KEY, APP_THEME_TO_DRAWIO_THEME[theme]);
}
