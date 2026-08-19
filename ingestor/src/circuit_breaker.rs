//! Circuit Breaker implementation for InfluxDB HTTP transport resiliency

use std::sync::Mutex;
use std::time::{Duration, Instant};
use tracing::{info, warn};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum State {
    Closed,
    Open,
    HalfOpen,
}

pub struct CircuitBreaker {
    failure_threshold: u32,
    cooldown_duration: Duration,
    state: Mutex<BreakerState>,
}

struct BreakerState {
    current_state: State,
    consecutive_failures: u32,
    opened_at: Option<Instant>,
}

impl CircuitBreaker {
    pub fn new(failure_threshold: u32, cooldown_duration: Duration) -> Self {
        Self {
            failure_threshold,
            cooldown_duration,
            state: Mutex::new(BreakerState {
                current_state: State::Closed,
                consecutive_failures: 0,
                opened_at: None,
            }),
        }
    }

    /// Determines whether an outbound HTTP request may proceed.
    pub fn can_execute(&self) -> bool {
        let mut state = self.state.lock().unwrap();
        match state.current_state {
            State::Closed => true,
            State::Open => {
                if let Some(opened_at) = state.opened_at {
                    if opened_at.elapsed() >= self.cooldown_duration {
                        info!("[CircuitBreaker] Cooldown expired; transitioning from Open to HalfOpen.");
                        state.current_state = State::HalfOpen;
                        return true;
                    }
                }
                false
            }
            State::HalfOpen => true, // Allow a single probe attempt
        }
    }

    pub fn current_state_code(&self) -> u32 {
        let state = self.state.lock().unwrap();
        match state.current_state {
            State::Closed => 0,
            State::HalfOpen => 1,
            State::Open => 2,
        }
    }

    /// Records a successful write and resets circuit state to Closed.
    pub fn record_success(&self) {
        let mut state = self.state.lock().unwrap();
        if state.current_state != State::Closed {
            info!("[CircuitBreaker] Successful request. Circuit reset to Closed state.");
        }
        state.current_state = State::Closed;
        state.consecutive_failures = 0;
        state.opened_at = None;
    }

    /// Records a failed write. Trips to Open state if failure threshold is reached.
    pub fn record_failure(&self) {
        let mut state = self.state.lock().unwrap();
        state.consecutive_failures += 1;
        warn!(
            "[CircuitBreaker] Upstream write failure recorded (consecutive: {}/{})",
            state.consecutive_failures, self.failure_threshold
        );

        if state.current_state == State::HalfOpen || state.consecutive_failures >= self.failure_threshold {
            state.current_state = State::Open;
            state.opened_at = Some(Instant::now());
            warn!(
                "[CircuitBreaker] Circuit TRIPPED to OPEN state! Blocking requests for {}s.",
                self.cooldown_duration.as_secs()
            );
        }
    }

    #[allow(dead_code)]
    pub fn state(&self) -> State {
        self.state.lock().unwrap().current_state
    }
}
