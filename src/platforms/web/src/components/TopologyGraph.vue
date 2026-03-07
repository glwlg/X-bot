<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps<{
  data: {
    nodes: Array<{
      id: string
      name: string
      type: string
      status: string
    }>
    edges: Array<{
      source: string
      target: string
      type: string
      value: number
    }>
  }
}>()

const chartContainer = ref<HTMLElement | null>(null)
let chart: echarts.ECharts | null = null

// Calculate node color based on status
const getNodeColor = (status: string) => {
  switch (status) {
    case 'healthy': return '#10b981' // emerald-500
    case 'warning': return '#f59e0b' // amber-500
    case 'critical': return '#ef4444' // red-500
    default: return '#64748b' // slate-500
  }
}

// Get symbol based on type (simplified)
const getNodeSymbol = (type: string) => {
  // ECharts supports 'circle', 'rect', 'roundRect', 'triangle', 'diamond', 'pin', 'arrow', 'none'
  // Could also implement custom SVG paths if needed
  switch (type) {
    case 'database': return 'roundRect'
    case 'cache': return 'diamond'
    case 'queue': return 'rect'
    case 'gateway': return 'pin'
    default: return 'circle'
  }
}

const initChart = () => {
  if (!chartContainer.value) return
  
  if (chart) {
    chart.dispose()
  }
  
  // Detect dark mode (simple check on html class or prop)
  const isDark = document.documentElement.classList.contains('dark')
  
  chart = echarts.init(chartContainer.value, isDark ? 'dark' : undefined)
  
  updateChartOption()
}

const updateChartOption = () => {
  if (!chart || !props.data) return

  const isDark = document.documentElement.classList.contains('dark')
  
  const nodes = props.data.nodes.map(node => ({
    id: node.id,
    name: node.name,
    value: node.status,
    symbol: getNodeSymbol(node.type),
    symbolSize: node.type === 'gateway' ? 50 : 35,
    itemStyle: {
      color: getNodeColor(node.status)
    },
    label: {
      show: true,
      position: 'bottom' as any,
      color: isDark ? '#e2e8f0' : '#1e293b'
    },
    category: node.type
  }))

  const edges = props.data.edges.map(edge => ({
    source: edge.source,
    target: edge.target,
    value: edge.value,
    lineStyle: {
      width: Math.min(Math.max(edge.value * 0.5, 1), 5), // Scale width by value (RPS)
      curveness: 0.2,
      color: isDark ? '#475569' : '#cbd5e1'
    }
  }))

  const option: echarts.EChartsOption = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      formatter: (params: any) => {
        if (params.dataType === 'node') {
          const statusText = params.data.value === 'healthy' ? '健康' : 
                             params.data.value === 'warning' ? '警告' : 
                             params.data.value === 'critical' ? '严重' : '未知'
          return `
            <div class="font-bold">${params.data.name}</div>
            <div>类型: ${params.data.category}</div>
            <div>状态: ${statusText}</div>
          `
        } else {
          return `
            <div>${params.data.source} → ${params.data.target}</div>
            <div>流量: ${params.data.value.toFixed(2)} RPS</div>
          `
        }
      }
    },
    series: [
      {
        type: 'graph',
        layout: 'force',
        data: nodes,
        links: edges,
        roam: true,
        label: {
          show: true
        },
        force: {
          repulsion: 400,
          gravity: 0.1,
          edgeLength: 150
        },
        lineStyle: {
          opacity: 0.7
        },
        emphasis: {
          focus: 'adjacency',
          lineStyle: {
            width: 4
          }
        }
      }
    ]
  }

  chart.setOption(option)
}

watch(() => props.data, () => {
  updateChartOption()
}, { deep: true })

const handleResize = () => {
  chart?.resize()
}

onMounted(() => {
  initChart()
  window.addEventListener('resize', handleResize)
  // Watch for theme changes if using a class observer
  const observer = new MutationObserver(() => {
    initChart() // Re-init to pick up theme
  })
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  chart?.dispose()
})
</script>

<template>
  <div ref="chartContainer" class="w-full h-full"></div>
</template>
