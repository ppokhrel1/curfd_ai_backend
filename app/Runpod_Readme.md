# curfdai_ml API

This service is a RunPod Serverless handler. The HTTP endpoint is provided by RunPod for your deployed worker. All API calls send a JSON body with an `input` object that includes an `action` field.

## API Paths (RunPod Serverless)

- `POST /run` (async job submission)
- `POST /runsync` (synchronous request)

## Base Request Shape

```json
{
  "input": {
    "action": "...",
    "...": "..."
  }
}
```

## Actions

### 1) process_requirements
Refines a user's prompt into a structured requirements JSON and returns updated chat history.

**Request payload**

```json
{
  "input": {
    "action": "process_requirements",
    "prompt": "Design a small quadcopter frame",
    "history": [
      {"role": "user", "content": "I need a lightweight drone"},
      {"role": "assistant", "content": "{\"model_type\":\"drone\",...}"}
    ]
  }
}
```

**Response body (success)**

```json
{
  "status": "success",
  "requirements": {
    "model_type": "drone",
    "primary_function": "A small quadcopter frame for hobby use",
    "description_natural_language": "A compact central body with four arms equally spaced...",
    "standard_components": [
      {"name": "Flight controller", "search_term": "stack 20x20 mm"}
    ],
    "custom_description": "Use 3 mm thick arms, 160 mm motor-to-motor..."
  },
  "history": [
    {"role": "user", "content": "Design a small quadcopter frame"},
    {"role": "assistant", "content": "{\"model_type\":\"drone\",...}"}
  ]
}
```

**Notes**
- `history` is optional. If omitted, it defaults to an empty list.
- `requirements` follows this schema:
  - `model_type`: `drone | robot | car | custom`
  - `primary_function`: string
  - `description_natural_language`: string
  - `standard_components`: array of `{ name, search_term }`
  - `custom_description`: string

---

### 2) generate_scad
Generates OpenSCAD output and uploads a zipped package to B2.

**Request payload**

```json
{
  "input": {
    "action": "generate_scad",
    "requirements_json": {
      "model_type": "drone",
      "primary_function": "A small quadcopter frame for hobby use",
      "description_natural_language": "A compact central body with four arms equally spaced...",
      "standard_components": [
        {"name": "Flight controller", "search_term": "stack 20x20 mm"}
      ],
      "custom_description": "Use 3 mm thick arms, 160 mm motor-to-motor..."
    }
  }
}
```

**Response body (success)**

```json
{
  "status": "success",
  "data": {
    "file_id": "b2_file_id",
    "filename": "assembly.zip",
    "download_url": "https://..."
  }
}
```

**Response body (error)**

```json
{
  "error": "No requirements_json provided"
}
```

---

### 3) health
Returns a basic health status and GPU availability.

**Request payload**

```json
{
  "input": {
    "action": "health"
  }
}
```

**Response body**

```json
{
  "status": "healthy",
  "gpu": true,
  "service_initialized": true
}
```

## Error Format
If an unexpected exception occurs, the handler returns:

```json
{
  "error": "<error message>"
}
```
