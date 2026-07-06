// Conventional Commits enforcement (https://www.conventionalcommits.org).
// Run locally by the .husky/commit-msg hook and in CI by .github/workflows/ci.yml.
module.exports = {
  extends: ['@commitlint/config-conventional'],
  rules: {
    // Scopes map loosely to the monorepo areas; keep the type set conventional.
    'type-enum': [
      2,
      'always',
      [
        'feat',
        'fix',
        'docs',
        'style',
        'refactor',
        'perf',
        'test',
        'build',
        'ci',
        'chore',
        'revert',
      ],
    ],
    'body-max-line-length': [0, 'always'], // allow long lines in bodies (URLs, logs)
  },
};
