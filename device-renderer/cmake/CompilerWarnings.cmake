# Shared warning flags so every target in this project gets consistent, strict
# diagnostics regardless of which platform/toolchain builds it.

function(eb_apply_warnings target)
  if(MSVC)
    target_compile_options(${target} PRIVATE /W4)
  else()
    target_compile_options(${target} PRIVATE
      -Wall
      -Wextra
      -Wpedantic
      -Wshadow
      -Wnon-virtual-dtor
      -Woverloaded-virtual
    )
  endif()
endfunction()
