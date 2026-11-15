{
 "ready": false,
 "known": {},
 "missing": [
  {
   "item": "target",
   "ask": "Application name, environment name, base URL, and confirmation you own / may load-test it"
  },
  {
   "item": "api_spec",
   "ask": "Do you have an OpenAPI/Swagger file or URL, Postman collection, GraphQL schema, HAR capture or .proto? (or 'none')"
  },
  {
   "item": "test_data",
   "ask": "Any test data files (CSV/JSON)? (or 'none')"
  },
  {
   "item": "auth",
   "ask": "Authentication type and ENV VAR NAMES only (values go in .env)"
  },
  {
   "item": "nfr",
   "ask": "Do approved NFRs/SLOs exist? (or 'none')"
  },
  {
   "item": "frontend",
   "ask": "Frontend URL and key user journeys? (or 'none')"
  }
 ],
 "secrets_rule": "Never paste passwords, tokens or API keys in chat. Put them in the project .env file (it is gitignored) and tell me only the variable name, e.g. PERF_API_TOKEN."
}
