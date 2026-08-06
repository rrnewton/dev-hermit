// TODAY: DetInode is a bare alias. The leak COMPILES.
pub type RawInode = u64;
pub type DetInode = RawInode;
enum ResourceID { FileContents(DetInode) }
fn main() {
    let raw_ino: Option<RawInode> = Some(221742951);           // a HOST inode
    let _r = raw_ino.map(ResourceID::FileContents);            // files.rs:969 shape
    println!("alias build: the raw host inode flowed into FileContents WITHOUT a diagnostic");
}
