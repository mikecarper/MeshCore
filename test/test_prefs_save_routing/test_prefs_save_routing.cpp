#include <gtest/gtest.h>

#include "helpers/PrefsSaveRouting.h"

namespace Routing = PrefsSaveRouting;

TEST(PrefsSaveRouting, CommonSetterWritesOnlyCommonImage) {
  constexpr Routing::Plan plan = Routing::planFor(Routing::Scope::Common);

  EXPECT_TRUE(plan.common);
  EXPECT_FALSE(plan.observer);
}

TEST(PrefsSaveRouting, ObserverSetterWritesOnlyObserverImage) {
  constexpr Routing::Plan plan = Routing::planFor(Routing::Scope::Observer);

  EXPECT_FALSE(plan.common);
  EXPECT_TRUE(plan.observer);
}

TEST(PrefsSaveRouting, CrossImageOperationCanWriteBothImages) {
  constexpr Routing::Plan plan = Routing::planFor(Routing::Scope::Both);

  EXPECT_TRUE(plan.common);
  EXPECT_TRUE(plan.observer);
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
