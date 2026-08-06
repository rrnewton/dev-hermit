// PROPOSED: DetInode is a newtype with ONE auditable conversion.
pub type RawInode = u64;
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct DetInode(u64);
impl DetInode {
    /// The ONLY way to mint a DetInode from a host inode. Call sites are auditable by grep.
    pub fn determinize_from_raw(_raw: RawInode, counter: u64) -> Self { DetInode(counter) }
    pub fn as_u64(self) -> u64 { self.0 }
}
enum ResourceID { FileContents(DetInode) }
fn main() {
    let raw_ino: Option<RawInode> = Some(221742951);
    let _r = raw_ino.map(ResourceID::FileContents);            // MUST NOT COMPILE
}
