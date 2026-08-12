/**
 * Jest configuration for frontend testing.
 *
 * Run tests with: npm test
 * Watch mode: npm test -- --watch
 */

export default {
    testEnvironment: 'jsdom',
    roots: ['<rootDir>/static/js'],
    testMatch: ['**/__tests__/**/*.test.js'],
    moduleNameMapper: {
        '^@/(.*)$': '<rootDir>/static/js/$1',
    },
    collectCoverageFrom: [
        'static/js/**/*.js',
        '!static/js/__tests__/**',
        '!static/js/vendor/**',
    ],
    coverageThreshold: {
        global: {
            branches: 50,
            functions: 50,
            lines: 50,
            statements: 50,
        },
    },
};
