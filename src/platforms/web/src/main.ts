import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'
import './assets/tailwind.css'
import './style.css'
import './styles/theme.css'

// 导入并初始化主题系统（在应用挂载前初始化）
import { useThemeStore } from './stores/theme'

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.use(router)

// 初始化主题（必须在pinia安装后，但在mount前）
const themeStore = useThemeStore()
themeStore.init()

app.mount('#app')
