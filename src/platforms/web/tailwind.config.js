/** @type {import('tailwindcss').Config} */
export default {
    content: [
        "./index.html",
        "./src/**/*.{vue,js,ts,jsx,tsx}",
    ],
    theme: {
        extend: {
            colors: {
                // Match Element Plus primary colors
                primary: {
                    50: '#ecf5ff',
                    100: '#d9ecff',
                    200: '#c6e2ff',
                    300: '#a0cfff',
                    400: '#79bbff',
                    500: '#409eff',
                    600: '#337ecc',
                    700: '#265f99',
                    800: '#1a3f66',
                    900: '#0d2033',
                },
                // Gradient colors from MainLayout
                purple: {
                    start: '#667eea',
                    end: '#764ba2',
                }
            },
        },
    },
    plugins: [],
    // Preflight can conflict with Element Plus, but we'll keep it and handle any issues
    corePlugins: {
        preflight: false, // Disable Tailwind's base reset to avoid conflicts with Element Plus
    },
}
