<template>
  <section class="page" data-module="monitor">
    <header class="page-head">
      <div>
        <h2>采集监控</h2>
        <p class="page-desc">
          按规则责任快照汇总各团队校验异常：只统计本责任范围的结论，越权结果不计入趋势。
        </p>
      </div>
      <div class="page-actions">
        <label class="filter-item">
          <span>责任团队</span>
          <select v-model="team" data-testid="monitor-team" @change="refreshSnapshot">
            <option value="">全部团队</option>
            <option v-for="item in teamOptions" :key="item" :value="item">{{ item }}</option>
          </select>
        </label>
        <button class="btn primary" data-testid="sync-snapshot" type="button" @click="syncSnapshot">
          同步待处理清单
        </button>
      </div>
    </header>
    <div class="analysis-summary">
      <article v-for="m in summaryMetrics" :key="m.label" class="analysis-card" data-testid="kpi-card">
        <span style="font-size:12px;color:var(--muted)">{{ m.label }}</span>
        <strong style="font-size:22px;display:block">{{ m.value }}</strong>
        <span :class="m.trend > 0 ? 'trend-up' : 'trend-down'" style="font-size:12px">{{ m.trend > 0 ? '↑' : '↓' }}{{ Math.abs(m.trend) }}%</span>
      </article>
    </div>

    <section class="snapshot-panel" data-testid="team-trend-panel">
      <header class="snapshot-head">
        <h3>按团队校验异常趋势</h3>
        <span data-testid="snapshot-batch">
          批次 {{ snapshotMeta?.批次号 ?? '尚未生成' }} · 待处理 {{ pendingCount }} 条 ·
          可回读记录 {{ readBacks.length }} 份
        </span>
      </header>
      <div v-if="teamTrend.length" class="trend-grid">
        <article
          v-for="item in teamTrend"
          :key="item.团队"
          class="trend-card"
          :data-testid="`trend-${item.团队}`"
        >
          <strong>{{ item.团队 }}</strong>
          <span>规则 {{ item.规则数 }} 条</span>
          <span class="trend-abnormal">异常 {{ item.异常数 }} 条</span>
          <span>复用 {{ item.复用数 }} 条 · 执行 {{ item.执行次数 }} 次</span>
        </article>
      </div>
      <p v-else class="empty-state">当前团队还没有可汇总的规则责任快照</p>
      <p v-if="syncMessage" class="scope-note" data-testid="sync-message">{{ syncMessage }}</p>
    </section>

    <section class="snapshot-panel" data-testid="pending-panel">
      <header class="snapshot-head">
        <h3>待处理清单</h3>
        <span>同一批次重复提交只保留首份确认</span>
      </header>
      <table class="data-table" v-if="pending.length">
        <thead>
          <tr>
            <th>批次号</th><th>规则编号</th><th>责任团队</th><th>采集源</th>
            <th>异常类型</th><th>阻断级别</th><th>处理状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in pending" :key="`${row.批次号}-${row.规则ID}`">
            <td>{{ row.批次号 }}</td>
            <td>{{ row.规则编号 }}</td>
            <td>{{ row.责任团队 }}</td>
            <td>{{ row.采集源 }}</td>
            <td>{{ row.异常类型 || '—' }}</td>
            <td>{{ row.阻断级别 || '—' }}</td>
            <td>{{ row.处理状态 }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="empty-state">待处理清单为空，先在校验页生成规则责任快照</p>
    </section>

    <section class="snapshot-panel" data-testid="readback-panel">
      <header class="snapshot-head">
        <h3>协作业务面监控服务回读记录</h3>
        <span>每次读取规则责任快照都会留一份可回读记录</span>
      </header>
      <table class="data-table" v-if="readBacks.length">
        <thead>
          <tr><th>批次号</th><th>读取团队</th><th>读取方</th><th>条目数</th><th>异常条目</th><th>读取次数</th></tr>
        </thead>
        <tbody>
          <tr v-for="row in readBacks" :key="row.id">
            <td>{{ row.批次号 }}</td>
            <td>{{ row.读取团队 }}</td>
            <td>{{ row.读取方 }}</td>
            <td>{{ row.条目数 }}</td>
            <td>{{ row.异常条目 }}</td>
            <td>{{ row.读取次数 }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="empty-state">还没有回读记录</p>
    </section>

    <form class="analysis-form" @submit.prevent="runAnalysis">
      <label v-for="field in paramFields" :key="field"><span>{{ field }}</span>
        <select v-if="field.includes('方法')||field.includes('策略')||field.includes('粒度')" v-model="params[field]"><option v-for="o in options[field]" :key="o" :value="o">{{ o }}</option></select>
        <input v-else v-model="params[field]" :placeholder="`请输入${field}`" />
      </label>
      <button class="btn primary" type="submit" data-testid="run-analysis">运行分析</button>
    </form>
    <div v-if="chartData.length" class="analysis-chart">
      <div v-for="(bar, i) in chartData" :key="i" class="analysis-bar" :style="{ height: barHeight(bar.value) + 'px' }"><span>{{ bar.label }}</span></div>
    </div>
    <table class="data-table" v-if="results.length">
      <thead><tr><th v-for="c in resultColumns" :key="c">{{ c }}</th></tr></thead>
      <tbody><tr v-for="row in results" :key="row.id"><td v-for="c in resultColumns" :key="c">{{ row[c] ?? '—' }}</td></tr></tbody>
    </table>
    <p v-if="!results.length && !loading" style="text-align:center;color:var(--muted)">请配置参数后运行分析</p>
  </section>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { request } from '@/api/client'
const ENDPOINT = '/api/monitor'
const analysisMetrics = ref<{label:string;value:number;trend:number}[]>([])
const summaryMetrics = computed(() => [
  ...analysisMetrics.value,
  { label: '待处理异常', value: pendingCount.value, trend: -pendingCount.value },
])
const results = ref<any[]>([]); const chartData = ref<{label:string;value:number}[]>([])
const loading = ref(false)
const params = ref<Record<string,string>>({})
const paramFields = ["面板编号", "采集源总数", "今日采集量", "成功率", "平均延迟", "失败重试", "异常告警", "更新时间"].filter((f: string) => !['编号','名称','版本'].includes(f)).slice(0, 4)
const options: Record<string,string[]> = { '统计粒度': ['按日','按周','按月'], '分组字段': ['全量','按类型','按区域'], '比较方法': ['环比','同比','绝对值'], '时间范围': ['最近7天','最近30天','最近90天'] }
const resultColumns = computed(() => ["面板编号", "采集源总数", "今日采集量", "成功率", "平均延迟", "失败重试", "异常告警", "更新时间"].slice(0, 5))

const team = ref('')
const teamOptions = ref<string[]>([])
const teamTrend = ref<{团队:string;规则数:number;异常数:number;复用数:number;执行次数:number}[]>([])
const snapshotMeta = ref<{批次号:string}|null>(null)
const pending = ref<any[]>([])
const pendingCount = ref(0)
const readBacks = ref<any[]>([])
const syncMessage = ref('')

function barHeight(v: number) { const max = Math.max(...chartData.value.map(d => d.value), 1); return Math.max(10, (v / max) * 140) }
function teamQuery() { return team.value ? `?team=${encodeURIComponent(team.value)}` : '' }
async function fetchSummary() { try { const res = await request(`${ENDPOINT}/summary`); analysisMetrics.value = (await res.json()).metrics || [] } catch(e) {} }
async function refreshSnapshot() {
  try {
    const res = await request(`${ENDPOINT}/rule-snapshot${teamQuery()}`)
    const data = await res.json()
    teamTrend.value = data.teams || []
    snapshotMeta.value = data.snapshot
    pendingCount.value = data.pending ?? 0
    if (!teamOptions.value.length) {
      teamOptions.value = Array.from(new Set((data.trend || []).map((t: any) => t.团队)))
    }
  } catch { teamTrend.value = [] }
  try {
    const res = await request(`${ENDPOINT}/rule-snapshot/pending${teamQuery()}`)
    const data = await res.json()
    pending.value = data.items || []
  } catch { pending.value = [] }
  try {
    const res = await request(`${ENDPOINT}/rule-snapshot/readbacks${teamQuery()}`)
    readBacks.value = (await res.json()).items || []
  } catch { readBacks.value = [] }
}
async function syncSnapshot() {
  try {
    const res = await request(`${ENDPOINT}/rule-snapshot/sync${teamQuery()}`, { method: 'POST' })
    const data = await res.json()
    if (!data.batch) {
      syncMessage.value = '还没有规则责任快照，请先在校验页按批次重算'
    } else if (data.duplicates > 0) {
      syncMessage.value = `批次 ${data.batch} 已存在待处理确认，重复提交只保留首份`
    } else {
      syncMessage.value = `批次 ${data.batch} 新增 ${data.synced} 条待处理`
    }
  } catch { syncMessage.value = '同步待处理清单失败' }
  await refreshSnapshot()
}
async function runAnalysis() {
  loading.value = true
  try { const res = await request(`${ENDPOINT}/run`, { method: 'POST', body: JSON.stringify({ values: params.value }) }); const data = await res.json(); results.value = data.items || []; chartData.value = (data.items || []).map((r: any) => ({ label: Object.values(r)[0] as string, value: Object.values(r).filter(v => typeof v === 'number')[0] as number || 0 })).slice(0, 6) } catch(e) { results.value = [] }
  loading.value = false
}
onMounted(async () => { await fetchSummary(); await refreshSnapshot() })
</script>
