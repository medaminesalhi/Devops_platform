import http from 'k6/http';
import { check } from 'k6';

export const options = {
  "scenarios": {
    "performance": {
      "executor": "constant-vus",
      "vus": 2,
      "duration": "30s",
      "gracefulStop": "5s"
    }
  },
  "thresholds": {
    "http_req_failed": [
      "rate<0.01000000"
    ],
    "http_req_duration": [
      "p(95)<500",
      "p(99)<1000"
    ],
    "checks": [
      "rate>0.99000000"
    ]
  },
  "summaryTrendStats": [
    "avg",
    "min",
    "med",
    "max",
    "p(90)",
    "p(95)",
    "p(99)",
    "count"
  ],
  "tags": {
    "testid": "8",
    "project_id": "10",
    "test_id": "7",
    "deployment_id": "none",
    "performance_mode": "basic"
  }
};

const TARGET_URL = "https://test.k6.io/";

export default function () {
  const response = http.get(TARGET_URL, {
    redirects: 5,
    tags: { endpoint: 'target' },
  });

  check(response, {
    'HTTP status < 400': (r) => r.status >= 200 && r.status < 400,
  });
}
