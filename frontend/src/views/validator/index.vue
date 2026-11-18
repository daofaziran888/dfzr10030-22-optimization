<template>
  <section class="page" data-module="validator">
    <header class="page-head">
      <div>
        <h2>采集结果校验管理</h2>
        <p class="page-desc">
          按责任范围复用校验结果：同一规则在一个批次里只执行一次，团队只读取本责任范围的规则责任快照。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记校验规则</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出采集结果校验清单</button>
      </div>
    </header>

    <form class="filter-bar scope-bar" @submit.prevent="rebuildSnapshot">
      <label class="filter-item">
        <span>责任视角</span>
        <select v-model="scopeTeam" data-testid="scope-team" @change="reload">
          <option value="">全部团队</option>
          <option v-for="team in teams" :key="team" :value="team">{{ team }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>当前角色</span>
        <select v-model="scopeRole" data-testid="scope-role" @change="reload">
          <option value="操作员">操作员</option>
          <option value="只读">只读</option>
        </select>
      </label>
      <label class="filter-item">
        <span>回查批次</span>
        <select v-model="batchFilter" data-testid="batch-filter" @change="reloadSnapshot">
          <option value="">最新批次</option>
          <option v-for="item in batches" :key="item.批次号" :value="item.批次号">
            {{ item.批次号 }}
          </option>
        </select>
      </label>
      <button class="btn primary" data-testid="rebuild-snapshot" type="submit" :disabled="readonly || rebuilding">
        {{ rebuilding ? '重算中…' : '按批次重算快照' }}
      </button>
      <span class="scope-hint" data-testid="snapshot-batch">
        批次：{{ snapshot?.批次号 ?? '尚未生成' }} · 执行 {{ snapshot?.执行规则数 ?? 0 }} 条 ·
        复用 {{ snapshot?.复用规则数 ?? 0 }} 条 · 规则 {{ snapshot?.规则总数 ?? 0 }} 条
      </span>
    </form>

    <p v-if="readonly" class="scope-note" data-testid="readonly-note">
      只读角色：只能查看本归属的校验规则与规则责任快照，不能重算批次或改动规则。
    </p>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>责任团队</th>
          <th>共享授权</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row['责任团队'] ?? '—' }}</td>
          <td>{{ row['共享授权'] || '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              :disabled="readonly"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 3" class="empty-state">
            当前责任范围内暂无采集结果校验数据
          </td>
        </tr>
      </tbody>
    </table>

    <section class="snapshot-panel" data-testid="snapshot-panel">
      <header class="snapshot-head">
        <h3>规则责任快照</h3>
        <span>
          规则 {{ snapshot?.规则总数 ?? 0 }} · 共享规则组 {{ snapshot?.共享规则组 ?? 0 }} ·
          合并复用组 {{ snapshot?.合并复用组 ?? 0 }} · 异常条目 {{ snapshot?.异常条目 ?? 0 }}
        </span>
      </header>
      <table class="data-table">
        <thead>
          <tr>
            <th>规则编号</th>
            <th>责任团队</th>
            <th>共享授权</th>
            <th>采集源</th>
            <th>样本版本</th>
            <th>冲突口径</th>
            <th>本轮口径</th>
            <th>执行次数</th>
            <th>校验结论</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="entry in snapshotEntries"
            :key="entry.规则ID"
            :data-testid="`snapshot-row-${entry.规则ID}`"
            :data-reuse="entry.复用结论"
          >
            <td>{{ entry.规则编号 }}</td>
            <td>{{ entry.责任团队 }}</td>
            <td>{{ entry.共享授权 || '—' }}</td>
            <td>{{ entry.采集源 }}</td>
            <td>{{ entry.样本版本 }}</td>
            <td>{{ entry.冲突模式 }}</td>
            <td>{{ entry.本轮口径 }}</td>
            <td>{{ entry.执行次数 }}</td>
            <td>{{ entry.校验结论 }}</td>
          </tr>
          <tr v-if="!snapshotEntries.length">
            <td colspan="9" class="empty-state">当前责任范围还没有可读取的规则责任快照</td>
          </tr>
        </tbody>
      </table>
      <p class="snapshot-foot">
        重算会生成新批次，旧批次原样保留，可随时按批次回查原依据。
      </p>
    </section>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记校验规则</h3>
        <label v-for="field in columns" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <label v-for="field in scopeFields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createScope[field]" :data-testid="`create-${field}`" />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条采集结果校验记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type SnapshotEntry = {
  规则ID: number
  规则编号: string
  责任团队: string
  共享授权: string
  采集源: string
  样本版本: string
  冲突模式: string
  本轮口径: string
  复用结论: string
  执行次数: number
  校验结论: string
}
type SnapshotMeta = {
  批次号: string
  规则总数: number
  执行规则数: number
  复用规则数: number
  共享规则组: number
  合并复用组: number
  异常条目: number
}

const ENDPOINT = '/api/validator'
const columns = ["规则编号", "校验字段", "校验类型", "期望值", "容错范围", "采样比例", "阻断级别", "规则状态"]
const scopeFields = ["责任团队", "共享授权", "采集源", "样本版本"]
const actions = ["提交测试", "启用规则", "停用规则"]
const statuses = ["草稿", "待测试", "已启用", "已停用"]
const metricNames = ["规则总数", "已启用规则", "待测试规则"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const createScope = ref<Record<string, string>>({})
const scopeTeam = ref('')
const scopeRole = ref('操作员')
const teams = ref<string[]>([])
const snapshot = ref<SnapshotMeta | null>(null)
const snapshotEntries = ref<SnapshotEntry[]>([])
const batches = ref<{ 批次号: string }[]>([])
const batchFilter = ref('')
const rebuilding = ref(false)

const readonly = computed(() => scopeRole.value === '只读')
const stats = computed(() => [
  { label: metricNames[0], value: total.value },
  { label: metricNames[1], value: rows.value.filter((row) => row.status === statuses[2]).length },
  { label: metricNames[2], value: rows.value.filter((row) => row.status === statuses[1]).length },
])

function scopeQuery(): string {
  const params = new URLSearchParams()
  if (scopeTeam.value) params.set('team', scopeTeam.value)
  if (scopeRole.value) params.set('role', scopeRole.value)
  return params.toString()
}

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  const params = new URLSearchParams(filters.value)
  const scope = new URLSearchParams(scopeQuery())
  scope.forEach((value, key) => params.set(key, value))
  try {
    const response = await request(`${ENDPOINT}/export?${params.toString()}`)
    if (!response.ok) throw new Error('导出失败，请稍后重试')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'validator.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(columns.map((field) => [field, '']))
  createScope.value = Object.fromEntries(scopeFields.map((field) => [field, '']))
  createScope.value['责任团队'] = scopeTeam.value
  createScope.value['样本版本'] = 'v2026.11'
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}?${scopeQuery()}`, {
      method: 'POST', body: JSON.stringify({ ...createValues.value, ...createScope.value }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '登记失败')
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions?${scopeQuery()}`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '采集结果校验动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集结果校验操作失败'
  }
}

async function reloadSnapshot() {
  const params = new URLSearchParams(scopeQuery())
  if (batchFilter.value) params.set('batch', batchFilter.value)
  try {
    const response = await request(`${ENDPOINT}/snapshot?${params.toString()}`)
    if (!response.ok) throw new Error('规则责任快照读取失败')
    const payload = await response.json()
    snapshot.value = payload.snapshot
    snapshotEntries.value = payload.entries ?? []
  } catch (error) {
    snapshot.value = null
    snapshotEntries.value = []
    errorMessage.value = error instanceof Error ? error.message : '规则责任快照读取失败'
  }
}

async function reloadBatches() {
  try {
    const response = await request(`${ENDPOINT}/snapshot/batches`)
    if (!response.ok) return
    batches.value = (await response.json()).items ?? []
  } catch {
    batches.value = []
  }
}

async function rebuildSnapshot() {
  if (readonly.value) {
    errorMessage.value = '只读角色不能重算规则责任快照'
    return
  }
  rebuilding.value = true
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/snapshot/rebuild?${scopeQuery()}`, {
      method: 'POST',
      body: JSON.stringify({ team: scopeTeam.value, role: scopeRole.value }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '快照重算失败')
    batchFilter.value = ''
    await Promise.all([reloadSnapshot(), reloadBatches()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '快照重算失败'
  } finally {
    rebuilding.value = false
  }
}

async function reload() {
  errorMessage.value = ''
  const params = new URLSearchParams(filters.value as Record<string, string>)
  new URLSearchParams(scopeQuery()).forEach((value, key) => params.set(key, value))
  try {
    const response = await request(`${ENDPOINT}?${params.toString()}`)
    if (!response.ok) {
      throw new Error('校验规则列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集结果校验列表读取失败'
  }
  await reloadSnapshot()
}

async function loadScopeOptions() {
  try {
    const response = await request(`${ENDPOINT}/scope-options`)
    if (!response.ok) return
    teams.value = (await response.json()).teams ?? []
  } catch {
    teams.value = []
  }
}

onMounted(async () => {
  await loadScopeOptions()
  await reloadBatches()
  await reload()
})
</script>
