# Hybrid Single-Node Chroma with S3 - MVP Specification

## Goal

Prove that single-node Chroma can successfully store and retrieve embeddings from S3 with acceptable performance, without requiring Kubernetes or distributed infrastructure.

## MVP Scope: The Absolute Minimum

### What's IN Scope ✅

**Core Feature**: Store vector data in S3 blockfiles, keep metadata in SQLite

```
┌─────────────────────────────────────────┐
│  Python Client                          │
└────────────────┬────────────────────────┘
                 │
┌────────────────▼────────────────────────┐
│  RustBindingsAPI (minimal changes)      │
└────────────────┬────────────────────────┘
                 │
      ┌──────────┴──────────┐
      │                     │
┌─────▼──────┐      ┌──────▼──────────────┐
│ SQLite     │      │ BlockfileProvider   │
│ (metadata) │      │ (vectors only)      │
└────────────┘      └──────┬──────────────┘
                           │
                    ┌──────▼──────┐
                    │   Storage   │
                    │   (S3)      │
                    └─────────────┘
```

**What Works**:
1. ✅ Add embeddings → Stored in S3 blockfiles
2. ✅ Query embeddings → Loaded from S3 (with cache)
3. ✅ Update/Delete embeddings → Applied to blockfiles
4. ✅ Restart persistence → Reload from S3
5. ✅ Basic caching → Memory cache for hot blocks

**Technology Stack**:
- **Metadata**: Keep in SQLite (no change)
- **Vectors**: Store in S3 blockfiles
- **WAL**: Keep SQLite-based (no change for MVP)
- **Compaction**: Manual trigger only (no background thread)
- **GC**: None (manual S3 cleanup)

### What's OUT of Scope ❌

**Explicitly NOT in MVP**:
- ❌ Background compaction (manual only)
- ❌ Automatic garbage collection
- ❌ Sophisticated WAL (keep SQLite)
- ❌ Disk cache (memory only)
- ❌ Migration tooling
- ❌ Multi-instance support
- ❌ Advanced monitoring/metrics
- ❌ Production-grade error handling
- ❌ Metadata in blockfiles (SQLite is fine for MVP)

**Why These Are Excluded**:
- Can be added iteratively
- Not essential to prove core concept
- Significantly reduce implementation time
- Users can live with manual operations for MVP

---

## MVP Architecture

### Storage Layout

```
Local:
  chroma.sqlite3              # All metadata + WAL
  .chroma_cache/              # Memory-mapped block cache
    {block_id}.cache

S3 (s3://my-bucket/chroma/):
  collections/
    {collection_id}/
      segments/
        {segment_id}/
          blockfiles/
            record.bf         # ID mappings
            vector.bf         # Vector data
          version.json        # Version metadata
```

### Component Changes

#### 1. **Python Bindings** (Minimal Changes)

```python
# chromadb/api/rust.py - ONLY add S3 config

class RustBindingsAPI(ServerAPI):
    def __init__(self, system: System):
        super().__init__(system)

        # NEW: Read S3 config
        use_s3 = self._system.settings.get("chroma_use_s3_storage", False)

        if use_s3:
            s3_config = {
                "bucket": self._system.settings.require("chroma_s3_bucket"),
                "region": self._system.settings.get("chroma_s3_region", "us-east-1"),
            }
        else:
            s3_config = None

        # Pass to Rust bindings
        self.bindings = chromadb_rust_bindings.Bindings(
            allow_reset=...,
            sqlite_db_config=...,
            persist_path=persist_path,
            hnsw_cache_size=self.hnsw_cache_size,
            s3_config=s3_config,  # NEW
        )
```

#### 2. **Rust Bindings** (Wire Up Existing Components)

```rust
// rust/python_bindings/src/bindings.rs

#[pymethods]
impl Bindings {
    #[new]
    #[pyo3(signature = (
        allow_reset,
        sqlite_db_config,
        hnsw_cache_size,
        persist_path=None,
        s3_config=None,  // NEW
    ))]
    pub fn py_new(
        allow_reset: bool,
        sqlite_db_config: SqliteDBConfig,
        hnsw_cache_size: usize,
        persist_path: Option<String>,
        s3_config: Option<S3Config>,  // NEW
    ) -> ChromaPyResult<Self> {
        let runtime = tokio::runtime::Runtime::new().unwrap();
        let _guard = runtime.enter();
        let system = System::new();
        let registry = Registry::new();

        // NEW: Configure storage backend
        let storage = match s3_config {
            Some(cfg) => {
                // Use S3 storage
                let s3_storage_config = chroma_storage::config::S3StorageConfig {
                    bucket: cfg.bucket,
                    credentials: cfg.credentials,
                    ..Default::default()
                };
                chroma_storage::Storage::try_from_config(
                    &chroma_storage::config::StorageConfig::S3(s3_storage_config)
                ).await?
            }
            None => {
                // Use local storage (existing behavior)
                chroma_storage::Storage::Local(
                    chroma_storage::LocalStorage::new(persist_path.clone())
                )
            }
        };

        // NEW: Configure blockfile provider with S3 storage
        let blockfile_config = if s3_config.is_some() {
            chroma_blockstore::config::ArrowBlockfileProviderConfig {
                block_manager_config: chroma_blockstore::arrow::config::BlockManagerConfig {
                    max_block_size_bytes: 8 * 1024 * 1024,  // 8MB blocks
                    block_cache_config: chroma_cache::CacheConfig::Memory(
                        chroma_cache::FoyerCacheConfig {
                            capacity: 100,  // Cache 100 blocks (~800MB)
                            ..Default::default()
                        }
                    ),
                    ..Default::default()
                },
                ..Default::default()
            }
        } else {
            // Keep existing local behavior
            Default::default()
        };

        // Register storage and blockfile provider in registry
        registry.register(storage);
        let blockfile_provider = chroma_blockstore::provider::BlockfileProvider::try_from_config(
            &(blockfile_config, storage.clone()),
            &registry
        ).await?;
        registry.register(blockfile_provider);

        // Rest of existing initialization...
        let frontend_config = FrontendConfig {
            // ... existing config ...
            segment_manager: Some(segment_manager_config),
        };

        let frontend = runtime.block_on(async {
            Frontend::try_from_config(&(frontend_config, system), &registry).await
        })?;

        Ok(Bindings { runtime, frontend })
    }
}
```

#### 3. **Configuration** (Simple Additions)

```python
# chromadb/config.py - Add S3 settings

# S3 Configuration
chroma_use_s3_storage: bool = False
chroma_s3_bucket: Optional[str] = None
chroma_s3_region: str = "us-east-1"
chroma_s3_access_key: Optional[str] = None  # Or use IAM roles
chroma_s3_secret_key: Optional[str] = None
```

#### 4. **Segment Manager** (Use Existing Blockfile Segments)

**Key Insight**: We DON'T need to write a new segment manager! The blockfile segments already exist in the codebase:

- `rust/segment/src/blockfile_record.rs` - Already handles ID mappings and data
- `rust/segment/src/distributed_hnsw.rs` - Already works with blockfiles

**Change Required**: Make `LocalSegmentManager` use blockfile segments when S3 is configured:

```rust
// rust/segment/src/local_segment_manager.rs - MINIMAL CHANGE

impl LocalSegmentManager {
    pub async fn get_hnsw_writer(
        &self,
        collection: &Collection,
        segment: &Segment,
        dimensionality: usize,
    ) -> Result<LocalHnswSegmentWriter, LocalSegmentManagerError> {
        // If blockfile provider is available (S3 mode), use blockfile-based HNSW
        // Otherwise, use existing local file-based HNSW

        if self.blockfile_provider.is_some() {
            // Use blockfile-based storage (writes to S3)
            LocalHnswSegmentWriter::from_blockfile(
                collection,
                segment,
                dimensionality,
                self.blockfile_provider.clone(),
            ).await?
        } else {
            // Existing behavior (local files)
            LocalHnswSegmentWriter::from_segment(
                collection,
                segment,
                dimensionality,
                self.persist_root.clone(),
                self.sqlite.clone(),
            ).await?
        }
    }
}
```

---

## MVP Usage

### For Users

```python
# Option 1: Local storage (existing behavior)
client = chromadb.PersistentClient(path="./chroma_data")

# Option 2: S3 storage (NEW!)
client = chromadb.PersistentClient(
    path="./chroma_data",  # Still needed for SQLite
    settings=chromadb.Settings(
        chroma_use_s3_storage=True,
        chroma_s3_bucket="my-chroma-vectors",
        chroma_s3_region="us-west-2",
    )
)

# Everything else works the same!
collection = client.create_collection("test")
collection.add(
    ids=["1", "2"],
    embeddings=[[1.0, 2.0], [3.0, 4.0]],
)
results = collection.query(query_embeddings=[[1.0, 2.0]], n_results=1)
```

### Manual Operations

```python
# Manual compaction (no background thread in MVP)
collection.compact()  # Flushes in-memory blocks to S3

# Manual cleanup (no automatic GC in MVP)
# User deletes old S3 files manually or via script
```

---

## MVP Limitations

### Known Constraints

1. **No Background Compaction**
   - User must call `collection.compact()` manually
   - Or trigger compaction on collection size threshold

2. **No Garbage Collection**
   - Old blockfiles accumulate in S3
   - User must manually delete old files (we can provide script)

3. **No WAL on S3**
   - SQLite WAL stays local
   - Durability limited to local disk + manual S3 backup

4. **Memory Cache Only**
   - No disk cache layer
   - Cache limited by available RAM

5. **No Migration Tooling**
   - User converts manually (copy data, recreate collections)

6. **Basic Error Handling**
   - S3 failures may cause crashes
   - No retry logic beyond basic S3 SDK

### Performance Expectations

| Operation | Expected Performance |
|-----------|---------------------|
| First query (cold) | 200-500ms |
| Cached query | 10-50ms (same as local) |
| Write | 1-10ms (local, async S3 flush) |
| Compaction | 1-5s per collection |
| Restart time | 2-10s (load metadata, warm cache) |

### Storage Costs (Example)

**Workload**: 1M vectors (768 dimensions), 10K queries/day
- **Storage**: ~3GB → $0.07/month
- **API calls**: ~300K GET/month → $0.12/month
- **Total**: ~$0.19/month

---

## Implementation Plan

### Week 1: Core Storage Integration

**Goal**: Get blockfile provider working with S3

```
Day 1-2: Python bindings changes
- Add s3_config parameter
- Pass through to Rust

Day 3-4: Rust bindings changes
- Wire up Storage with S3
- Configure BlockfileProvider
- Register in DI container

Day 5: Testing
- Unit tests for S3 storage
- Integration test with MinIO
```

**Deliverable**: Can initialize Chroma with S3 blockfile provider

### Week 2: Segment Integration

**Goal**: Make HNSW segments write to S3 blockfiles

```
Day 1-3: LocalSegmentManager changes
- Detect when blockfile provider available
- Use blockfile-based HNSW writer
- Implement read/write operations

Day 4-5: Testing
- Add vectors, verify written to S3
- Query vectors, verify loaded from S3
- Restart, verify persistence
```

**Deliverable**: Full CRUD operations work with S3 storage

### Week 3: Polish & Documentation

**Goal**: Make it usable and document limitations

```
Day 1-2: Error handling
- Handle S3 connection failures gracefully
- Add validation for S3 config
- Improve error messages

Day 3-4: Manual compaction
- Add collection.compact() method
- Expose in Python API
- Document when to use

Day 5: Documentation
- Setup guide (S3 credentials, bucket creation)
- Usage examples
- Limitations and workarounds
- Migration from local to S3
```

**Deliverable**: MVP ready for beta testing

---

## Success Criteria

### Must Have ✅

1. ✅ **Functional**: Add, query, update, delete operations work
2. ✅ **Persistent**: Data survives restart
3. ✅ **S3 Storage**: Vectors stored in S3, not local disk
4. ✅ **Performance**: Cached queries within 2x of local performance
5. ✅ **Documentation**: Clear setup guide and limitations

### Nice to Have 🎯

1. 🎯 Cache hit rate >70% on typical workloads
2. 🎯 Cold start <10 seconds
3. 🎯 Manual compaction script
4. 🎯 S3 cost estimation tool
5. 🎯 Basic monitoring (cache stats, S3 API calls)

### Not Required ❌

1. ❌ Automatic background operations
2. ❌ Production-grade error recovery
3. ❌ Multi-instance coordination
4. ❌ Advanced caching strategies
5. ❌ Migration automation

---

## After MVP: Iteration Path

### MVP → v0.2: Automation (2-3 weeks)

1. Add background compaction thread
2. Add basic garbage collection
3. Improve error handling

### v0.2 → v0.3: Advanced Features (3-4 weeks)

1. Implement SimpleWAL (S3-synced WAL)
2. Add disk cache layer
3. Smart cache warming

### v0.3 → v1.0: Production Ready (3-4 weeks)

1. Migration tooling
2. Monitoring and metrics
3. Comprehensive testing
4. Production documentation

**Total Timeline**:
- MVP: **3 weeks**
- v0.2: **+3 weeks** (6 total)
- v0.3: **+4 weeks** (10 total)
- v1.0: **+4 weeks** (14 total)

---

## Quick Start (MVP)

### Setup S3 Bucket

```bash
aws s3 mb s3://my-chroma-vectors --region us-west-2
```

### Install Chroma (with MVP patch)

```bash
pip install chromadb
# Or install from branch with MVP changes
```

### Use S3 Storage

```python
import chromadb
from chromadb.config import Settings

# Configure S3
client = chromadb.PersistentClient(
    path="./chroma_data",
    settings=Settings(
        chroma_use_s3_storage=True,
        chroma_s3_bucket="my-chroma-vectors",
    )
)

# Use normally
collection = client.create_collection("docs")
collection.add(
    ids=["doc1"],
    embeddings=[[1.0, 2.0, 3.0]],
    metadatas=[{"source": "web"}],
)

results = collection.query(
    query_embeddings=[[1.0, 2.0, 3.0]],
    n_results=1
)
print(results)

# Manual operations
collection.compact()  # Flush to S3
```

### Verify S3 Storage

```bash
aws s3 ls s3://my-chroma-vectors/chroma/collections/ --recursive
```

---

## Risk Mitigation

### Technical Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| S3 latency too high | Medium | High | Aggressive caching, larger blocks |
| Blockfile integration breaks | Low | High | Comprehensive testing, rollback plan |
| Cache misses cause OOM | Medium | Medium | LRU eviction, configurable cache size |
| S3 costs exceed expectations | Low | Medium | Cost monitoring, documentation |

### User Experience Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Configuration too complex | Medium | Medium | Smart defaults, validation, docs |
| Manual compaction forgotten | High | Medium | Auto-trigger on size threshold |
| No visibility into S3 usage | High | Low | Basic logging, cost estimation tool |

---

## Open Questions

1. **Cache Size**: What default memory cache size? (Propose: 1GB or 10% of RAM)
2. **Block Size**: 8MB blocks or 16KB? (Propose: 8MB for fewer S3 calls)
3. **Compaction Trigger**: Auto-trigger at what threshold? (Propose: 10K records or 100MB)
4. **S3 Prefix**: Allow custom prefix? (Propose: Yes, default "chroma/")
5. **Credentials**: Support all AWS auth methods? (Propose: IAM role + env vars for MVP)

---

## Summary

### MVP in One Sentence

**Store Chroma vector data in S3 blockfiles instead of local files, keep metadata in SQLite, require manual compaction.**

### Key Trade-offs

| Dimension | MVP Choice | Full Version |
|-----------|-----------|--------------|
| Time to Build | 3 weeks | 15 weeks |
| Features | Core only | Complete |
| Automation | Manual | Automatic |
| Production Ready | Beta | Yes |
| User Complexity | Medium | Low |

### Why This MVP?

1. ✅ **Proves core concept**: Can we store/load from S3?
2. ✅ **Minimal changes**: Leverage existing blockfile code
3. ✅ **Fast to build**: 3 weeks vs. 15 weeks
4. ✅ **Real value**: Users get S3 durability immediately
5. ✅ **Clear iteration path**: Each step adds value

### Next Step

Should we proceed with implementing this MVP? I can start with Week 1 (Core Storage Integration) right away!
