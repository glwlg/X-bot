# Frontend Template

这是一个基于 Vue 3 + Vite + TailwindCSS 4 的极简前端模板项目。
内置了基础的页面 Layout 结构、路由守卫、Pinia 状态管理以及基于 Shadcn (Radix Vue) 的高品质 UI 组件库集成。

## 特性
- ⚡️ **Vite 5**：提供极速冷启动和热重载。
- 🎨 **TailwindCSS 4**：基于原子 CSS 的极致样式开发体验。
- 🧩 **UI 库集成**：保留了原项目中的 `src/components/ui` 基础组件，可以直接用于搭建现代后台。
- 🔒 **自带鉴权体系**：`src/stores/auth.ts` 内置了对接后端的心跳机制与 JWT Token 生命周期管理。
- ⚙️ **自动主题适配**：原生支持深色模式。

## 目录结构
- `src/components/ui/`: 通用 UI 基础组件封装。
- `src/layouts/`: 全局布局组合，提供包含侧边栏在内的 `MainLayout.vue`。
- `src/router/`: Vue-Router 配置，已预置前置权限守卫白名单机制。
- `src/stores/`: Pinia Store 模块隔离。
- `src/views/`: 业务页面层。已预留 `HomeView` 和 `LoginView`。

## 快速开始

1. **环境安装**
推荐使用 Node.js 20+。
```bash
npm install
```

2. **启动开发服务器**
```bash
npm run dev
```

3. **构建生产包**
```bash
npm run build
```

## 注意事项
如果在打包阶段遇到 `vue-tsc` 校验报错，请确保新增页面的 TS 类型补全。
