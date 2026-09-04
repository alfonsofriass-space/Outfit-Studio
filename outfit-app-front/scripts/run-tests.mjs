import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import os from 'node:os'

const environment = { ...process.env }
if (process.platform === 'linux' && os.release().toLowerCase().includes('microsoft')) {
  // Algunas terminales de WSL heredan TEMP/TMP de Windows. Los workers de
  // Vitest necesitan un temporal nativo de Linux para crear sus directorios.
  Object.assign(environment, { TMPDIR: '/tmp', TMP: '/tmp', TEMP: '/tmp' })
}

const vitest = fileURLToPath(
  new URL('../node_modules/vitest/vitest.mjs', import.meta.url),
)
const result = spawnSync(process.execPath, [vitest, 'run'], {
  env: environment,
  stdio: 'inherit',
})

if (result.error) {
  console.error(result.error.message)
  process.exit(1)
}
process.exit(result.status ?? 1)
