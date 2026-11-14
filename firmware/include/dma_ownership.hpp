#pragma once
#include <cstddef>
#include <cstdint>

namespace mvb {
// Main-task ownership bookkeeping for persistent DMA buffers/descriptors.
// Submitting hardware work and processing completion must use this same task.
// Slots become writable only after the matching FIFO completion is consumed.
template <unsigned SlotCount> class BasicDmaOwnership {
public:
  static constexpr unsigned Slots = SlotCount, InvalidSlot = Slots;
  static_assert(Slots >= 2, "Two SPI queue slots are required");

private:
  unsigned queue_[Slots]{};
  unsigned head_ = 0, pending_ = 0, depth_ = 1;
  bool owned_[Slots]{}, faulted_ = false;
  uint32_t leased_[Slots]{}, next_lease_ = 0;
  unsigned active_leases_ = 0;
  uint64_t submissions_ = 0, completions_ = 0, errors_ = 0;
  bool reject() {
    errors_++;
    faulted_ = true;
    return false;
  }

public:
  explicit BasicDmaOwnership(unsigned depth = 1) {
    if (depth == 1 || depth == 2)
      depth_ = depth;
    else
      reject();
  }
  unsigned depth() const { return depth_; }
  unsigned pending() const { return pending_; }
  bool owns(unsigned slot) const { return slot < Slots && owned_[slot]; }
  bool leased(unsigned slot) const { return slot < Slots && leased_[slot]; }
  bool writable(unsigned slot) const {
    return !faulted_ && slot < Slots && !owned_[slot] && !leased_[slot];
  }
  unsigned active_leases() const { return active_leases_; }
  bool matches_lease(unsigned slot, uint32_t token) const {
    return slot < Slots && token && leased_[slot] == token;
  }
  uint32_t lease(unsigned slot) {
    if (!writable(slot)) {
      reject();
      return 0;
    }
    if (!++next_lease_)
      ++next_lease_;
    leased_[slot] = next_lease_;
    ++active_leases_;
    return next_lease_;
  }
  bool release_lease(unsigned slot, uint32_t token) {
    if (!matches_lease(slot, token))
      return reject();
    leased_[slot] = 0;
    --active_leases_;
    return true;
  }
  unsigned oldest() const { return pending_ ? queue_[head_] : InvalidSlot; }
  bool faulted() const { return faulted_; }
  uint64_t submissions() const { return submissions_; }
  uint64_t completions() const { return completions_; }
  uint64_t errors() const { return errors_; }
  bool can_submit(unsigned slot) const {
    return writable(slot) && pending_ < depth_;
  }
  bool submit(unsigned slot) {
    if (faulted_)
      return false;
    if (!can_submit(slot))
      return reject();
    queue_[(head_ + pending_) % Slots] = slot;
    owned_[slot] = true;
    pending_++;
    submissions_++;
    return true;
  }
  bool submit_lease(unsigned slot, uint32_t token) {
    if (faulted_ || !matches_lease(slot, token) || owns(slot) ||
        pending_ >= depth_)
      return reject();
    // Hardware must already have accepted this exact persistent descriptor.
    // The caller invalidates its lease only after this ownership transition.
    leased_[slot] = 0;
    --active_leases_;
    return submit(slot);
  }
  bool complete(unsigned slot) {
    if (!pending_ || slot >= Slots || queue_[head_] != slot || !owned_[slot])
      return reject();
    // A fault does not prevent consuming a later, genuinely matched completion
    // for cleanup. It still prohibits new submissions until safe reset.
    owned_[slot] = false;
    head_ = (head_ + 1) % Slots;
    pending_--;
    completions_++;
    return true;
  }
  bool reset(bool hardware_quiescent) {
    return reset(hardware_quiescent, depth_);
  }
  bool reset(bool hardware_quiescent, unsigned depth) {
    // Clearing bookkeeping cannot stop DMA. Only the driver can establish this
    // precondition after draining/terminating the actual hardware work.
    if (!hardware_quiescent || active_leases_ || (depth != 1 && depth != 2))
      return reject();
    depth_ = depth;
    head_ = pending_ = 0;
    for (unsigned slot = 0; slot < Slots; ++slot)
      owned_[slot] = false;
    faulted_ = false;
    return true;
  }
};
using DmaOwnership = BasicDmaOwnership<2>;
} // namespace mvb
