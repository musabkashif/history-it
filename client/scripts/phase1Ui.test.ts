import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'
import { allowModalDismiss } from '../src/components/Modal.tsx'
import { pickJobActivity } from '../src/lib/formatTime.ts'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

function source(relative: string) {
  return readFileSync(join(root, relative), 'utf8')
}

test('create-project API payload shape is unchanged', () => {
  const api = source('src/lib/api.ts')
  assert.match(api, /controlCreateProject:\s*\(project_name:\s*string,\s*client_organization\?:\s*string\)/)
  assert.match(api, /JSON\.stringify\(\{\s*project_name,\s*client_organization:\s*client_organization \|\| undefined\s*\}\)/)
})

test('import-job API payload shape is unchanged', () => {
  const api = source('src/lib/api.ts')
  assert.match(api, /controlImport:\s*\(body:\s*ControlImportRequest\)/)
  assert.match(api, /JSON\.stringify\(body\)/)
  const types = source('src/lib/types.ts')
  assert.match(types, /export interface ControlImportRequest/)
  assert.match(types, /job_id:\s*string/)
  assert.match(types, /source:\s*'historical'\s*\|\s*'imported'/)
})

test('create-job form payload fields remain project, folder, spreadsheet, batch_size, box', () => {
  const types = source('src/lib/types.ts')
  assert.match(types, /export interface JobForm \{\s*project:\s*string;\s*source_folder_id:\s*string;\s*spreadsheet_id:\s*string;\s*batch_size:\s*number;\s*box:\s*string\s*\}/)
  const page = source('src/pages/NewJob.tsx')
  assert.match(page, /project:\s*lockedName \|\| form\.project/)
  assert.match(page, /create\(payload\)/)
})

test('Projects page opens create modal and keeps controlCreateProject call', () => {
  const page = source('src/pages/Projects.tsx')
  assert.match(page, /api\.controlCreateProject\(name,\s*organization\.trim\(\) \|\| undefined\)/)
  assert.match(page, /<Modal[\s\S]*Create a project/)
  assert.doesNotMatch(page, /surface mt-6 p-5[\s\S]*Create a project/)
  assert.match(page, /Create project/)
})

test('Jobs page opens import modal and keeps controlImport payload fields', () => {
  const page = source('src/pages/Jobs.tsx')
  assert.match(page, /api\.controlImport\(\{/)
  assert.match(page, /job_id:\s*importForm\.job_id\.trim\(\)/)
  assert.match(page, /register_url:\s*importForm\.register_url\.trim\(\) \|\| undefined/)
  assert.match(page, /register_file_reference:\s*importForm\.register_file_reference\.trim\(\) \|\| undefined/)
  assert.match(page, /source:\s*importForm\.source/)
  assert.match(page, /deployed_release:\s*importForm\.deployed_release\.trim\(\) \|\| undefined/)
  assert.match(page, /Import job/)
  assert.doesNotMatch(page, /Import previous job<\/h2>/)
})

test('read-only project context locks the project field when opened from a project', () => {
  const page = source('src/pages/NewJob.tsx')
  assert.match(page, /projectLocked/)
  assert.match(page, /readOnly/)
  assert.match(page, /aria-readonly="true"/)
  const app = source('src/main.tsx')
  assert.match(app, /projectLocked=\{openedFromProject && Boolean\(draftProject\.trim\(\)\)\}/)
})

test('modal Escape and backdrop dismiss are blocked while busy', () => {
  assert.equal(allowModalDismiss(false), true)
  assert.equal(allowModalDismiss(true), false)
  const modal = source('src/components/Modal.tsx')
  assert.match(modal, /allowModalDismiss\(busy\)/)
  assert.match(modal, /Escape/)
})

test('loading states use PageLoading instead of warning-styled loading alerts', () => {
  const projects = source('src/pages/Projects.tsx')
  const jobs = source('src/pages/Jobs.tsx')
  const control = source('src/pages/ControlJob.tsx')
  assert.match(projects, /PageLoading/)
  assert.match(jobs, /PageLoading/)
  assert.match(control, /PageLoading/)
  assert.doesNotMatch(projects, /title=\{view === 'loading' \? 'Loading'/)
  assert.doesNotMatch(projects, /kind=\{view === 'error' \? 'danger' : 'warning'\}[\s\S]*Loading projects/)
})

test('empty states expose Create/Import actions', () => {
  const projects = source('src/pages/Projects.tsx')
  const jobs = source('src/pages/Jobs.tsx')
  assert.match(projects, /EmptyState[\s\S]*action=\{createAction\}/)
  assert.match(jobs, /EmptyState[\s\S]*action=\{\([\s\S]*\{importButton\}[\s\S]*\{createButton\}/)
})

test('project and job metadata use ActivityMeta without inventing timestamps', () => {
  const projects = source('src/pages/Projects.tsx')
  const jobs = source('src/pages/Jobs.tsx')
  assert.match(projects, /ActivityMeta[\s\S]*createdAt=\{row\.created_at\}[\s\S]*updatedAt=\{row\.updated_at\}/)
  assert.match(jobs, /pickJobActivity\(row\)/)
  assert.match(jobs, /ActivityMeta/)
  assert.deepEqual(pickJobActivity({}), { created: null, activity: null, activityLabel: 'Updated' })
})

test('Authorize modal reuses shared Modal component', () => {
  const job = source('src/pages/Job.tsx')
  assert.match(job, /<Modal[\s\S]*Authorize folder or parent box/)
  assert.doesNotMatch(job, /fixed inset-0 z-40 flex items-center justify-center bg-black\/60/)
})

test('browser-history routing was not introduced', () => {
  const app = source('src/main.tsx')
  assert.doesNotMatch(app, /BrowserRouter|createBrowserRouter|useNavigate|window\.history\.pushState/)
  const layout = source('src/components/layout.tsx')
  assert.doesNotMatch(layout, /BrowserRouter|useNavigate/)
})
