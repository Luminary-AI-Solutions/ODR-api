# Firestore: CPE (`cpe_tasks`)

This API’s **backend** uses the **Firebase Admin SDK** (service account from `FIREBASE_SERVICE_ACCOUNT_KEY_JSON`) to create and update documents. It **bypasses** security rules.

Your **frontend** does **not** need to write task results for the pipeline to work: the server appends profiles and status as the run progresses.

---

## Collection

**`cpe_tasks`**

Document ID = **`task_id`** (UUID string). The client learns it from the WebSocket message (`INITIALIZING` / `TASK_ID`) or from your own correlation.

---

## Document fields (observed in code)

| Field | Type | When |
|--------|------|------|
| `taskId` | string | Create |
| `query` | string | Create |
| `location` | string \| omitted | Create |
| `maxSearchTasks` | number \| omitted | Create |
| `status` | string | Updated throughout | 
| `createdAt` | Timestamp | Create |
| `startedAt` | Timestamp | When processing starts |
| `updatedAt` | Timestamp | Various updates |
| `profiles` | array of maps | Appended per company (`ArrayUnion`) during extraction |
| `profileCount` | number | Near end |
| `processedDomainCount` | number | Near end |
| `usageStatistics` | map | Final usage (token/cost/search counts) |
| `completedProcessingAt` | Timestamp | Orchestrator completion |
| `completedAt` | Timestamp | Route-level completion |
| `error` | string | On failures |
| `stoppedReason` | string | If cancelled |

**Status values** you may see include (non-exhaustive):  
`PENDING`, `PROCESSING`, `PLANNING_COMPLETE`, `EXTRACTING`, `COMPLETED_PROCESSING`, `COMPLETED`, `ERROR`, `CANCELLED`.

Exact enums evolve with orchestration; treat `status` as a string and branch on known values in your UI.

---

## Firebase Console setup

1. Create or select a **Firebase project** (same project as your service account).
2. Enable **Cloud Firestore** (Native mode).
3. Create a **service account** key (JSON) and download it; set **`FIREBASE_SERVICE_ACCOUNT_KEY_JSON`** to that file path on the API host (never commit the file).
4. Deploy the API with that env var so `initialize_firebase_sync()` succeeds.

---

## If the frontend reads Firestore directly

Use the **Firebase JS SDK** (or Flutter, etc.) with the **client** config (`apiKey`, `projectId`, … from Project settings).  
Then add **security rules** so users only read what they should. Example pattern (adapt to your auth):

```
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    // Option A: locked down — no client access; only backend (Admin SDK) writes
    match /cpe_tasks/{taskId} {
      allow read, write: if false;
    }

    // Option B: read-only for authenticated users who own the task
    // (requires you to store ownerUid on the document from the backend)
    match /cpe_tasks/{taskId} {
      allow read: if request.auth != null
        && resource.data.ownerUid == request.auth.uid;
      allow write: if false;
    }
  }
}
```

Today’s API does not set `ownerUid` on `cpe_tasks`; add that in the API if you want per-user rules.

**Recommended for most apps:** keep Firestore **private** (`allow read: if false`) and let the frontend load results via **GET `/cpe/result/{task_id}`** after the WebSocket run. That avoids exposing raw DB access and keeps one path for authorization (your API gateway).

---

## Indexes

Start without composite indexes; add them only if you query collections with multiple `where` + `orderBy`. Polling by document ID does not require indexes.

---

## Related

- Deep Research collection: **`research_tasks`** — see `firestore_schema.md`.
