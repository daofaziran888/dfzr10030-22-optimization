import { expect, test } from '@playwright/test'

/**
 * 规则责任快照：按责任范围复用校验结果。
 *
 * 这里直接走接口链路（/api 由 dev server 代理到后端），验证三件事：
 * 1. 同一共享规则在一个批次里只执行一次，其余团队条目复用该结论；
 * 2. 团队只能读到自己责任范围内的规则与快照，越权读取/动作被拒绝；
 * 3. 监控服务按责任快照汇总异常、同步待处理清单，重复同步只保留首份确认。
 */

type SnapshotEntry = {
  规则ID: number
  规则编号: string
  责任团队: string
  共享授权: string
  冲突模式: string
  本轮口径: string
  复用结论: string
  执行次数: number
  校验结论: string
}

async function rebuild(request: any): Promise<{ 批次号: string; 条目: SnapshotEntry[] }> {
  const response = await request.post('/api/validator/snapshot/rebuild', { data: {} })
  expect(response.ok()).toBeTruthy()
  const payload = await response.json()
  expect(payload.ok).toBeTruthy()
  return payload.entry
}

test('规则责任快照：共享规则只执行一次并按团队隔离读取', async ({ request }) => {
  const snapshot = await rebuild(request)
  expect(snapshot.批次号).toMatch(/^BATCH-\d+$/)

  // 同一采集源、同一样本版本、同签名的两条团队规则，双团队互授后合并复用。
  const shared = snapshot.条目.filter(
    (entry) => entry.共享授权.length > 0 && entry.冲突模式 === '共享规则合并',
  )
  expect(shared.length).toBeGreaterThanOrEqual(2)
  expect(shared.filter((entry) => entry.复用结论 === '执行').length).toBe(1)
  expect(shared.filter((entry) => entry.复用结论 === '复用').length).toBe(shared.length - 1)
  expect(shared.every((entry) => entry.本轮口径 === '计算复用前置')).toBeTruthy()
  // 复用条目不再贡献执行次数，同一规则不会被重复执行。
  expect(shared.reduce((total, entry) => total + entry.执行次数, 0)).toBe(1)

  // 团队视角只读取本责任范围：数据采集组看不到其他团队独占的规则。
  const teamView = await request.get('/api/validator/snapshot?team=数据采集组')
  const teamPayload = await teamView.json()
  const visible = teamPayload.entries as SnapshotEntry[]
  expect(visible.length).toBeGreaterThan(0)
  expect(
    visible.every((entry) => entry.责任团队 === '数据采集组'
      || entry.共享授权.split(',').includes('数据采集组')),
  ).toBeTruthy()

  // 不在任何授权里的团队读不到条目，也不能登记/动作越权规则。
  const outsider = await (await request.get('/api/validator/snapshot?team=外部审计组')).json()
  expect(outsider.total).toBe(0)
  const outsiderList = await (await request.get('/api/validator?team=外部审计组')).json()
  expect(outsiderList.total).toBe(0)

  const denied = await request.post('/api/validator/3/actions?team=数据质量组', {
    data: { action: '启用规则' },
  })
  const deniedPayload = await denied.json()
  expect(deniedPayload.ok).toBeFalsy()
  expect(String(deniedPayload.message)).toContain('不能复用')

  // 只读角色能读本责任范围，但不能重算批次或执行动作。
  const readonlyRead = await (await request.get('/api/validator/snapshot?team=数据采集组&role=只读')).json()
  expect(readonlyRead.total).toBeGreaterThan(0)
  const readonlyRebuild = await (await request.post('/api/validator/snapshot/rebuild', {
    data: { role: '只读' },
  })).json()
  expect(readonlyRebuild.ok).toBeFalsy()
  const readonlyAction = await (await request.post('/api/validator/1/actions?team=数据采集组&role=只读', {
    data: { action: '停用规则' },
  })).json()
  expect(readonlyAction.ok).toBeFalsy()

  // 重算保留原依据：旧批次仍可按批次号回查。
  const batches = await (await request.get('/api/validator/snapshot/batches')).json()
  expect(batches.items.map((item: { 批次号: string }) => item.批次号)).toContain(snapshot.批次号)
  const replay = await (await request.get(`/api/validator/snapshot?batch=${snapshot.批次号}`)).json()
  expect(replay.snapshot.批次号).toBe(snapshot.批次号)
  expect(replay.entries.length).toBe(snapshot.条目.length)
})

test('监控服务：按责任快照汇总团队异常，待处理清单只保留首份确认', async ({ request }) => {
  await rebuild(request)

  const summary = await (await request.get('/api/monitor/rule-snapshot')).json()
  expect(summary.snapshot.批次号).toBeTruthy()
  expect(Array.isArray(summary.teams)).toBeTruthy()
  expect(summary.teams.length).toBeGreaterThan(0)
  // 每条团队汇总都能对上快照里的责任归属，越权条目不会被算进趋势。
  const teamNames = summary.entries?.map((entry: SnapshotEntry) => entry.责任团队) ?? []
  for (const bucket of summary.teams) {
    expect(bucket.规则数).toBeGreaterThan(0)
    expect(bucket.异常数).toBeLessThanOrEqual(bucket.规则数)
    if (teamNames.length) expect(teamNames).toContain(bucket.团队)
  }

  // 指定团队后只汇总该团队获授权的条目：自己负责的 + 共享授权给它的，
  // 其他团队独占的规则不会出现在它的趋势里。
  const teamSummary = await (await request.get('/api/monitor/rule-snapshot?team=数据采集组')).json()
  expect(teamSummary.teams.some((bucket: { 团队: string }) => bucket.团队 === '数据采集组')).toBeTruthy()
  const visibleOwners = new Set(
    teamSummary.teams.map((bucket: { 团队: string }) => bucket.团队),
  )
  const sharedOwners = await (await request.get('/api/validator/snapshot?team=数据采集组')).json()
  const allowedOwners = new Set(
    (sharedOwners.entries as SnapshotEntry[]).map((entry) => entry.责任团队),
  )
  for (const owner of visibleOwners) expect(allowedOwners.has(owner)).toBeTruthy()

  // 重复同步同一批次只保留首份待处理确认。
  const first = await (await request.post('/api/monitor/rule-snapshot/sync')).json()
  const second = await (await request.post('/api/monitor/rule-snapshot/sync')).json()
  expect(first.batch).toBeTruthy()
  expect(second.synced).toBe(0)
  expect(second.duplicates).toBeGreaterThanOrEqual(0)

  const pending = await (await request.get('/api/monitor/rule-snapshot/pending')).json()
  const keys = pending.items.map((row: { 批次号: string; 规则ID: number }) => `${row.批次号}-${row.规则ID}`)
  expect(new Set(keys).size).toBe(keys.length)

  // 协作业务面监控服务每次读取都留一份可回读记录。
  const readBacks = await (await request.get('/api/monitor/rule-snapshot/readbacks')).json()
  expect(readBacks.items.length).toBeGreaterThan(0)
  expect(readBacks.items[0].读取方).toBe('协作业务面监控服务')

  const chain = await (await request.get('/api/monitor/rule-snapshot/chain')).json()
  expect(chain.links.length).toBeGreaterThanOrEqual(3)
  expect(chain.links.map((link: { 环节: string }) => link.环节)).toContain('协作业务面监控服务')
})

test('校验页与监控页：责任范围、批次与团队趋势可见', async ({ page }) => {
  test.setTimeout(120_000)
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('response', (response) => {
    if (response.url().includes('/api/') && response.status() >= 400) {
      errors.push(`${response.status()} ${response.url()}`)
    }
  })

  await page.goto('/validator')
  await expect(page.getByTestId('snapshot-panel')).toBeVisible()
  await page.getByTestId('rebuild-snapshot').click()
  await expect(page.getByTestId('snapshot-batch')).toContainText('BATCH-')
  await expect(page.locator('tbody tr[data-testid^="snapshot-row-"]').first()).toBeVisible()

  await page.getByTestId('scope-team').selectOption('数据采集组')
  await page.waitForTimeout(800)
  await expect(page.locator('tbody tr[data-testid^="snapshot-row-"]').first()).toBeVisible()

  await page.getByTestId('scope-role').selectOption('只读')
  await expect(page.getByTestId('readonly-note')).toBeVisible()
  await expect(page.getByTestId('rebuild-snapshot')).toBeDisabled()

  await page.goto('/monitor')
  await expect(page.getByTestId('team-trend-panel')).toBeVisible()
  await expect(page.getByTestId('pending-panel')).toBeVisible()
  await page.getByTestId('sync-snapshot').click()
  await expect(page.getByTestId('sync-message')).toBeVisible()

  expect(errors).toEqual([])
})
