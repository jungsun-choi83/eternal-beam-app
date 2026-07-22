import https from 'https'
import fs from 'fs'

const source = process.argv[2] || 'https://device.eternalbeam.com/assets/index-BEPe4xLD.js'

async function load(u) {
  if (u.startsWith('http')) {
    return new Promise((resolve, reject) => {
      https.get(u, (r) => {
        let d = ''
        r.on('data', (c) => (d += c))
        r.on('end', () => resolve(d))
      }).on('error', reject)
    })
  }
  return fs.readFileSync(u, 'utf8')
}

const content = await load(source)
console.log('source:', source)
console.log('length:', content.length)

for (const p of ['onboarding', 'qrConnection', 'forestExperience', 'experience', 'forest', 'kickstarter']) {
  const re = new RegExp(p, 'g')
  const matches = [...content.matchAll(re)]
  console.log(p, 'count', matches.length, matches[0] ? `@${matches[0].index}` : '')
}

for (const re of [
  /return"onboarding"/g,
  /return"qrConnection"/g,
  /return'onboarding'/g,
  /return'qrConnection'/g,
  /useState\("onboarding"\)/g,
  /useState\("qrConnection"\)/g,
  /useState\('onboarding'\)/g,
  /useState\('qrConnection'\)/g,
  /experience==="forest"/g,
  /demo==="device"/g,
  /get\("experience"\)/g,
  /get\("demo"\)/g,
]) {
  const m = [...content.matchAll(re)]
  console.log(String(re), m.length, m[0]?.index ?? '')
}

const forestIdx = content.indexOf('/forest')
if (forestIdx >= 0) console.log('/forest snippet:', content.slice(forestIdx - 40, forestIdx + 120))
