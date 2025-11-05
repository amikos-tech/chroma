# Hybrid Single-Node Chroma with S3 - REFINED MVP Specification

## Critical Discovery

After investigating the complete storage architecture, I discovered that:

1. **Distributed Chroma stores ALL data in S3 blockfiles**, not just vectors:
   - Metadata (string, bool, f32, u32 indices)
   - Full-text search indices
   - **Sparse indices (BM25/sparse vector search)**
   - Record mappings
   - Dense vector indices (HNSW/SPANN)

2. **Single-node currently uses SQLite for metadata**, which limits:
   - No sparse vector search support
   - No S3 durability for metadata
   - SQLite file size constraints

3. **The solution**: Use distributed's blockfile-based segments in single-node mode!

---

## Refined MVP Goal

**Migrate single-node Chroma to use the same blockfile-based segment architecture as distributed, but without the distributed infrastructure (Kubernetes, gRPC, multiple services).**

---

## Architecture Comparison

### Current Single-Node ❌
```
Collection
├─ Sqlite Segment (LOCAL)
│  ├─ Metadata tables
│  ├─ Full-text tables
│  └─ Documents
│
└─ HnswLocalPersisted (LOCAL FILES)
   └─ 5 HNSW files per index
```

**Problems**:
- All metadata in SQLite (single point of failure)
- No sparse vector search
- No S3 durability
- Local file size limits

### Distributed ✅
```
Collection
├─ BlockfileMetadata → S3
│  ├─ Full-text index
│  ├─ String metadata index
│  ├─ Bool/F32/U32 indices
│  └─ Sparse index (BM25)
│
├─ BlockfileRecord → S3
│  ├─ User ID mappings
│  └─ Raw records
│
└─ HnswDistributed → S3
   └─ HNSW vector index
```

**Dependencies**:
- Kubernetes
- Multiple services (query, compaction, GC)
- gRPC for coordination
- Complex deployment

### Refined MVP (Single-Node with S3) ✅
```
Collection
├─ BlockfileMetadata → S3 ⭐ NEW!
│  ├─ Full-text index
│  ├─ String metadata index
│  ├─ Bool/F32/U32 indices
│  └─ Sparse index (BM25) ⭐ NEW!
│
├─ BlockfileRecord → S3 ⭐ NEW!
│  ├─ User ID mappings
│  └─ Raw records
│
└─ HnswDistributed → S3 ⭐ NEW!
   └─ HNSW vector index

SQLite (Minimal)
├─ System catalog (tenants, databases, collections)
└─ WAL (keep for MVP)
```

**Benefits**:
- ✅ ALL data in S3 (metadata + vectors)
- ✅ Sparse vector search support (BM25)
- ✅ Uses proven distributed segment code
- ✅ Single process (no Kubernetes)
- ✅ Clear path to distributed (same segment format)

---

## What's Changing in Refined MVP

### Scope Expansion

**Original MVP** (too narrow):
- ❌ Only vectors to S3
- ❌ Keep metadata in SQLite
- ❌ No sparse index support

**Refined MVP** (complete):
- ✅ **ALL data to S3** (metadata + vectors + records)
- ✅ **Sparse index support** (BM25/text search)
- ✅ **Use distributed segment implementations**
- ✅ Keep SQLite only for system catalog + WAL

### Why This is Better

1. **Complete S3 migration**: No split storage, all data durable
2. **Feature parity**: Get sparse vector search for free
3. **Code reuse**: Use existing `BlockfileMetadataWriter` instead of SQLite
4. **Migration path**: Same segment format as distributed

---

## Refined Implementation Plan

### Week 1: Segment Infrastructure (Updated)

#### Day 1-2: Blockfile Provider Setup
- Configure `BlockfileProvider` with S3 storage
- Set up block cache (memory-based for MVP)
- Register in dependency injection

**Code**: `rust/python_bindings/src/bindings.rs`

#### Day 3-4: Switch Metadata Segment
- Create `BlockfileMetadataWriter` instead of `SqliteMetadataWriter`
- Migrate metadata operations to blockfile-based
- Handle full-text search via blockfile indices

**Code**:
- `rust/frontend/src/impls.rs` (Frontend implementation)
- `rust/segment/src/blockfile_metadata.rs` (already exists!)

#### Day 5: Switch Record Segment
- Create `BlockfileRecordWriter` for ID mappings
- Store raw data in blockfiles

**Code**: `rust/segment/src/blockfile_record.rs` (already exists!)

### Week 2: Vector Segment Migration (Updated)

#### Day 1-3: HNSW Blockfile Migration
- Use `HnswDistributed` segment instead of `HnswLocalPersisted`
- Configure to write to S3 blockfiles
- Ensure cache layer works for queries

**Code**: `rust/segment/src/distributed_hnsw.rs` (already exists!)

#### Day 4-5: Integration Testing
- Full CRUD operations with all segments on S3
- Restart persistence verification
- Sparse vector search testing ⭐ NEW!

### Week 3: Polish & Documentation (Same)

#### Day 1-2: Error Handling
- S3 connection failures
- Config validation
- Better error messages

#### Day 3-4: Manual Operations
- `collection.compact()` to flush all segments
- Expose segment stats in API

#### Day 5: Documentation
- Setup guide
- Configuration reference
- Limitations and workarounds
- **Sparse vector search examples** ⭐ NEW!

---

## Storage Layout

### S3 Structure

```
s3://my-bucket/chroma/
  collections/
    {collection_id}/
      {tenant}/
        {database_id}/
          segments/
            metadata/
              {segment_id}/
                blockfiles/
                  full_text_pls.bf       # Full-text posting lists
                  string_metadata.bf     # String metadata index
                  bool_metadata.bf       # Bool metadata index
                  f32_metadata.bf        # F32 metadata index
                  u32_metadata.bf        # U32 metadata index
                  sparse_max.bf          # Sparse index maximums ⭐
                  sparse_offset_value.bf # Sparse vector values ⭐
            record/
              {segment_id}/
                blockfiles/
                  user_id_to_id.bf       # ID mappings
                  id_to_user_id.bf       # Reverse mappings
                  id_to_data.bf          # Raw records
            vector/
              {segment_id}/
                blockfiles/
                  hnsw_index.bf          # HNSW graph
                  vector_data.bf         # Dense embeddings
```

### Local (Minimal)

```
./chroma_data/
  chroma.sqlite3          # System catalog + WAL only
  .chroma_cache/          # Memory-mapped block cache
    {block_id}.cache
```

---

## Code Changes Required

### 1. Frontend Configuration

**File**: `rust/frontend/src/config.rs`

```rust
pub struct FrontendConfig {
    // ... existing fields ...

    // NEW: Enable blockfile segments for single-node
    pub use_blockfile_segments: bool,

    // NEW: Storage backend configuration
    pub storage_config: Option<chroma_storage::config::StorageConfig>,
}
```

### 2. Python Bindings

**File**: `rust/python_bindings/src/bindings.rs:69-138`

```rust
#[pymethods]
impl Bindings {
    #[new]
    #[pyo3(signature = (
        allow_reset,
        sqlite_db_config,
        hnsw_cache_size,
        persist_path=None,
        s3_config=None,          // NEW
        use_blockfile_segments=False, // NEW
    ))]
    pub fn py_new(
        allow_reset: bool,
        sqlite_db_config: SqliteDBConfig,
        hnsw_cache_size: usize,
        persist_path: Option<String>,
        s3_config: Option<S3Config>,
        use_blockfile_segments: bool,  // NEW
    ) -> ChromaPyResult<Self> {
        // ... existing code ...

        // NEW: Configure storage backend
        let storage = if let Some(cfg) = s3_config {
            chroma_storage::Storage::S3(S3Storage::from_config(cfg))
        } else {
            chroma_storage::Storage::Local(LocalStorage::new(persist_path))
        };

        // NEW: Configure blockfile provider
        let blockfile_provider = if use_blockfile_segments {
            Some(BlockfileProvider::try_from_config(&blockfile_config).await?)
        } else {
            None
        };

        // NEW: Register in DI container
        registry.register(storage.clone());
        if let Some(provider) = blockfile_provider {
            registry.register(provider);
        }

        // Configure frontend
        let frontend_config = FrontendConfig {
            use_blockfile_segments,  // NEW
            storage_config: Some(storage),  // NEW
            // ... rest of config ...
        };

        // ... rest of implementation ...
    }
}
```

### 3. Segment Creation Logic

**File**: `rust/frontend/src/impls.rs`

**Change**: When creating collection segments, use blockfile types if enabled:

```rust
async fn create_collection_segments(
    &self,
    collection_id: CollectionUuid,
) -> Result<CollectionAndSegments> {
    if self.config.use_blockfile_segments {
        // NEW: Create blockfile-based segments
        let metadata_segment = Segment {
            id: SegmentUuid::new(),
            r#type: SegmentType::BlockfileMetadata,  // ⭐
            scope: SegmentScope::METADATA,
            collection: collection_id,
            file_path: HashMap::new(),
        };

        let record_segment = Segment {
            id: SegmentUuid::new(),
            r#type: SegmentType::BlockfileRecord,  // ⭐
            scope: SegmentScope::RECORD,
            collection: collection_id,
            file_path: HashMap::new(),
        };

        let vector_segment = Segment {
            id: SegmentUuid::new(),
            r#type: SegmentType::HnswDistributed,  // ⭐ (not HnswLocalPersisted)
            scope: SegmentScope::VECTOR,
            collection: collection_id,
            file_path: HashMap::new(),
        };
    } else {
        // Existing: Create SQLite-based segments
        // ...
    }
}
```

### 4. Python API

**File**: `chromadb/api/rust.py:77-125`

```python
class RustBindingsAPI(ServerAPI):
    def __init__(self, system: System):
        super().__init__(system)

        # NEW: Read S3 config
        use_s3 = self._system.settings.get("chroma_use_s3_storage", False)
        use_blockfile_segments = use_s3  # Enable blockfile segments with S3

        s3_config = None
        if use_s3:
            s3_config = {
                "bucket": self._system.settings.require("chroma_s3_bucket"),
                "region": self._system.settings.get("chroma_s3_region", "us-east-1"),
                "credentials": self._system.settings.get("chroma_s3_credentials", "AWS"),
            }

        # Pass to Rust bindings
        self.bindings = chromadb_rust_bindings.Bindings(
            allow_reset=...,
            sqlite_db_config=...,
            persist_path=persist_path,
            hnsw_cache_size=self.hnsw_cache_size,
            s3_config=s3_config,  # NEW
            use_blockfile_segments=use_blockfile_segments,  # NEW
        )
```

---

## Feature Additions

### Sparse Vector Search Support ⭐

With the refined MVP, users get sparse vector search (BM25) for free!

```python
# Create collection with sparse vector support
collection = client.create_collection(
    "docs",
    schema={
        "sparse_vector": {
            "type": "sparse_vector",
            "dimension": 10000,  # Vocabulary size
        }
    }
)

# Add documents with sparse vectors (e.g., TF-IDF or BM25)
collection.add(
    ids=["doc1", "doc2"],
    sparse_vectors=[
        {0: 0.5, 42: 0.8, 100: 0.3},  # doc1 sparse vector
        {5: 0.6, 42: 0.4, 200: 0.9},  # doc2 sparse vector
    ],
    metadatas=[{"source": "web"}, {"source": "pdf"}],
)

# Query with sparse vector (hybrid search)
results = collection.query(
    query_sparse_vector={42: 1.0, 100: 0.5},
    n_results=10,
)
```

**Implementation**: Uses Block-Max WAND algorithm from `rust/index/src/sparse/` backed by S3 blockfiles!

---

## Updated Timeline

### Week 1: Blockfile Segment Migration
- **Days 1-2**: Configure blockfile provider + S3 storage
- **Days 3-4**: Migrate metadata segment to blockfiles
- **Day 5**: Migrate record segment to blockfiles

### Week 2: Vector & Integration
- **Days 1-3**: Migrate HNSW to distributed/blockfile implementation
- **Days 4-5**: Integration testing (CRUD + sparse vectors)

### Week 3: Polish & Documentation
- **Days 1-2**: Error handling and validation
- **Day 3-4**: Manual compaction, expose stats
- **Day 5**: Documentation and examples

**Total**: **3 weeks** (same timeline, more complete!)

---

## Success Criteria (Updated)

### Must Have ✅

1. ✅ **All data in S3**: Metadata, records, vectors
2. ✅ **Full CRUD**: Add, query, update, delete operations
3. ✅ **Persistence**: Data survives restart
4. ✅ **Sparse vector search**: BM25/text search works ⭐ NEW!
5. ✅ **Performance**: Cached queries within 2x of local
6. ✅ **Documentation**: Setup guide and examples

### Nice to Have 🎯

1. 🎯 Cache hit rate >70%
2. 🎯 Cold start <10 seconds
3. 🎯 Manual compaction script
4. 🎯 Sparse vector search examples ⭐ NEW!
5. 🎯 Cost estimation tool

---

## Risks & Mitigations

### Technical Risks (Updated)

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Blockfile metadata slower than SQLite | Medium | Medium | Aggressive caching, prefetching |
| Sparse index adds complexity | Low | Low | Already implemented, well-tested |
| S3 latency too high | Medium | High | Larger blocks, smart caching |
| Breaking existing users | Low | High | Feature flag, backward compatibility |

### Migration Strategy

**Backward Compatibility**:
```python
# Old way (still works)
client = chromadb.PersistentClient(path="./data")

# New way (opt-in)
client = chromadb.PersistentClient(
    path="./data",
    settings=Settings(chroma_use_s3_storage=True, ...)
)
```

**Migration path**:
1. Users opt-in to S3 storage
2. Can run both modes side-by-side (different collections)
3. Migration tool to convert SQLite → blockfiles (post-MVP)

---

## Comparison with Original MVP

| Aspect | Original MVP | Refined MVP |
|--------|-------------|-------------|
| **Scope** | Vectors only to S3 | ALL data to S3 |
| **Metadata** | Keep in SQLite | Migrate to blockfiles |
| **Sparse Index** | Not included | ✅ Included |
| **Code Reuse** | Custom integration | Use distributed segments |
| **Timeline** | 3 weeks | 3 weeks (same) |
| **Complexity** | Medium | Medium-High |
| **Value** | Partial S3 | Complete S3 |
| **Migration Path** | Harder (split storage) | Easier (same format) |

**Decision**: Refined MVP is better because:
1. ✅ Complete solution (not partial)
2. ✅ Reuses proven distributed code
3. ✅ Adds sparse vector search
4. ✅ Same timeline
5. ✅ Easier migration to distributed later

---

## Next Steps

1. **Approval**: Review refined scope (all data → S3)
2. **Implementation**: Start Week 1 (blockfile segment migration)
3. **Testing**: Comprehensive tests for all segment types
4. **Documentation**: Examples for sparse vector search

**Ready to proceed with refined MVP?**

The key insight is: **Don't reinvent the wheel - use distributed's proven segment implementations in single-node mode!**
