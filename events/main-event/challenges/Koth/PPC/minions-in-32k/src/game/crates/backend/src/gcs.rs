use anyhow::{Context, Result};
use object_store::gcp::GoogleCloudStorageBuilder;
use object_store::path::Path as ObjectPath;
use object_store::{ObjectStoreExt, PutPayload};
use std::sync::Arc;

#[derive(Clone)]
pub struct GcsStore {
    bucket: Arc<str>,
    store: object_store::gcp::GoogleCloudStorage,
}

impl GcsStore {
    pub fn new(bucket: String) -> Result<Self> {
        let store = GoogleCloudStorageBuilder::from_env()
            .with_bucket_name(&bucket)
            .build()
            .context("build GCS object store")?;
        Ok(Self {
            bucket: Arc::from(bucket),
            store,
        })
    }

    pub fn bucket(&self) -> &str {
        &self.bucket
    }

    pub async fn get(&self, object: &str) -> Result<Option<Vec<u8>>> {
        let path = object_path(object)?;
        match self.store.get(&path).await {
            Ok(result) => Ok(Some(
                result
                    .bytes()
                    .await
                    .with_context(|| format!("read gs://{}/{}", self.bucket, path))?
                    .to_vec(),
            )),
            Err(object_store::Error::NotFound { .. }) => Ok(None),
            Err(e) => Err(e).with_context(|| format!("get gs://{}/{}", self.bucket, path)),
        }
    }

    pub async fn put(&self, object: &str, bytes: Vec<u8>) -> Result<()> {
        let path = object_path(object)?;
        self.store
            .put(&path, PutPayload::from(bytes))
            .await
            .with_context(|| format!("put gs://{}/{}", self.bucket, path))?;
        Ok(())
    }
}

fn object_path(object: &str) -> Result<ObjectPath> {
    ObjectPath::parse(object.trim_matches('/')).context("invalid GCS object path")
}
