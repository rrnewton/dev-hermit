#[derive(shmem_pod::PodValue)]
struct GenericDrop<T: 'static> {
    value: T,
}

impl<T> Drop for GenericDrop<T> {
    fn drop(&mut self) {}
}

fn main() {}
