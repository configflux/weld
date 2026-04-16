use crate::models::{User, Repository};

pub fn get_user(repo: &dyn Repository, id: u64) -> Option<User> {
    repo.find_by_id(id)
}

pub fn create_user(repo: &dyn Repository, user: &User) -> Result<(), String> {
    repo.save(user)
}
